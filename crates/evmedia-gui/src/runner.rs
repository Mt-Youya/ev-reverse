//! Running a batch: the worker loop, and the handle that can end it now.
//!
//! The queue owns the rows; this module owns the processes. It is the only place that decides how
//! many lessons run at once, when one is settled, and — when a graceful stop is not enough — how to
//! end one without waiting for it.

use crate::{
    job::{self, JobStatus, Running},
    plan::Options,
    protocol::{JobSink, Sink},
    queue::Queue,
};
use std::{
    collections::BTreeMap,
    sync::Arc,
    time::{Duration, Instant},
};

/// How often the loop looks at its running processes. Fast enough that a finished lesson frees a
/// worker slot almost immediately, slow enough to be free.
const TICK: Duration = Duration::from_millis(200);

/// A handle onto a running CLI, for the one operation that must not wait for it.
///
/// The loop owns the `Child` and polls it; this holds the process id and, on Windows, a job object,
/// so "stop now" can kill the tree without borrowing the child out from under the thread that is
/// watching it. The job object matters: the CLI spawns ffmpeg partway through a merge, and killing
/// only the CLI would leave the encoder holding the output file.
#[derive(Clone)]
pub struct KillSwitch {
    pub id: String,
    pub pid: u32,
    #[cfg(windows)]
    job: Option<Arc<crate::winjob::JobHandle>>,
}

impl KillSwitch {
    /// Windows gets the job object as well, so the kill reaches the ffmpeg the CLI spawned.
    #[cfg(windows)]
    pub fn new(id: String, pid: u32, job: Option<Arc<crate::winjob::JobHandle>>) -> Self {
        Self { id, pid, job }
    }

    #[cfg(not(windows))]
    pub fn new(id: String, pid: u32) -> Self {
        Self { id, pid }
    }

    /// Kill this run and everything it started. Returns whether anything was asked to die.
    pub fn terminate(&self) -> bool {
        #[cfg(windows)]
        {
            if let Some(job) = &self.job {
                job.terminate();
                return true;
            }
            // No job object: it could not be created, or the CLI was already inside a job that
            // forbids nesting. Fall back to the process itself — worse, because an ffmpeg the CLI
            // spawned survives — but better than refusing to stop.
            use windows_sys::Win32::{
                Foundation::CloseHandle,
                System::Threading::{OpenProcess, TerminateProcess, PROCESS_TERMINATE},
            };
            unsafe {
                let handle = OpenProcess(PROCESS_TERMINATE, 0, self.pid);
                if handle == 0 {
                    return false;
                }
                let killed = TerminateProcess(handle, 1) != 0;
                CloseHandle(handle);
                killed
            }
        }
        #[cfg(not(windows))]
        {
            std::process::Command::new("kill")
                .args(["-9", &self.pid.to_string()])
                .status()
                .map(|status| status.success())
                .unwrap_or(false)
        }
    }
}

/// Run workers until the queue is empty. Both the window and the tests use this one loop.
pub fn run(queue: &Queue, sink: &Arc<dyn Sink>, cli: &str, options: &Options, count: usize) {
    let mut running: BTreeMap<String, Running> = BTreeMap::new();
    let mut stopping = false;

    loop {
        // Reap first: a finished job should free its slot before another starts.
        let finished: Vec<String> = running
            .iter_mut()
            .filter_map(|(id, process)| match process.poll() {
                job::Poll::Working => None,
                job::Poll::Exited { .. } => Some(id.clone()),
            })
            .collect();
        for id in finished {
            let Some(mut process) = running.remove(&id) else { continue };
            // The status the CLI reported arrives on the same pipe as everything else, and a process
            // exits before the parent has necessarily read its last line. Settling the job before
            // the readers finish would turn a finished export into a failure.
            process.drain();
            queue.finish(&id);
            // The switch goes with the process: a forced stop must never reach a run that has
            // already been settled, and its pid could by now belong to something else.
            queue.forget_worker(&id);
        }

        let (queued, halt, requested) = {
            let state = queue.lock();
            let queued: Vec<String> = state
                .items
                .iter()
                .filter(|entry| entry.status == JobStatus::Queued)
                .map(|entry| entry.item.id.clone())
                .collect();
            (queued, state.halt.clone(), state.stop_requested)
        };

        stopping |= requested || halt.is_some();
        if stopping {
            for process in running.values() {
                let _ = process.request_stop();
            }
        }

        if queued.is_empty() && running.is_empty() {
            break;
        }

        let slots = count.saturating_sub(running.len());
        if !stopping && slots > 0 {
            for id in queued.into_iter().take(slots) {
                match queue.claim(&id) {
                    Ok(Some(entry)) => {
                        let job_sink: Arc<dyn Sink> =
                            Arc::new(JobSink::new(queue.clone(), sink.clone(), id.clone()));
                        match job::spawn(cli, &entry.item, options, &job_sink) {
                            Ok(process) => {
                                let pid = process.pid;
                                #[cfg(windows)]
                                let switch = KillSwitch::new(id.clone(), pid, process.job.clone());
                                #[cfg(not(windows))]
                                let switch = KillSwitch::new(id.clone(), pid);
                                queue.patch(&id, |stored| {
                                    stored.status = JobStatus::Running;
                                    stored.pid = Some(pid);
                                    stored.message = "已启动".to_string();
                                });
                                running.insert(id, process);
                                // Published so a forced stop can reach this run without borrowing it
                                // from the thread that is polling it.
                                queue.lock().workers.push(switch);
                            }
                            Err(error) => queue.patch(&id, |stored| {
                                stored.status = JobStatus::Failed;
                                stored.message = error;
                            }),
                        }
                    }
                    // Skipped: nothing to spawn, and the row already says why.
                    Ok(None) => {}
                    Err(error) => queue.patch(&id, |stored| {
                        stored.status = JobStatus::Failed;
                        stored.message = error;
                    }),
                }
            }
            continue;
        }

        std::thread::sleep(TICK);
    }

    queue.settle();
    sink.finished();
}

/// Wait until every worker has stopped. For the tests and the headless harness; the window never
/// waits, it listens.
pub fn wait_idle(queue: &Queue, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    let mut state = queue.lock();
    while state.running {
        let left = deadline.saturating_duration_since(Instant::now());
        if left.is_zero() {
            return false;
        }
        let (guard, _) = queue
            .changed()
            .wait_timeout(state, left.min(TICK))
            .unwrap_or_else(|error| error.into_inner());
        state = guard;
    }
    true
}
