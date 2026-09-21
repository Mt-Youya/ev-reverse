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
use evmedia_contract::ExportPhase;
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
    let mut downloads: BTreeMap<String, Running> = BTreeMap::new();
    let mut merges: BTreeMap<String, Running> = BTreeMap::new();
    let mut publishes: BTreeMap<String, Running> = BTreeMap::new();
    let mut ready_to_merge = std::collections::BTreeSet::new();
    let mut ready_to_publish = std::collections::BTreeSet::new();
    let mut stopping = false;

    loop {
        // Reap downloads first. A completed lesson joins the merge queue immediately; it never
        // waits for unrelated lessons still using the network workers.
        let finished_downloads: Vec<String> = downloads
            .iter_mut()
            .filter_map(|(id, process)| match process.poll() {
                job::Poll::Working => None,
                job::Poll::Exited { .. } => Some(id.clone()),
            })
            .collect();
        for id in finished_downloads {
            let Some(mut process) = downloads.remove(&id) else {
                continue;
            };
            // The status the CLI reported arrives on the same pipe as everything else, and a process
            // exits before the parent has necessarily read its last line. Settling the job before
            // the readers finish would turn a finished export into a failure.
            process.drain();
            let completed_download = queue
                .snapshot()
                .items
                .iter()
                .find(|entry| entry.item.id == id)
                .is_some_and(|entry| entry.status == JobStatus::Complete);
            if completed_download {
                ready_to_merge.insert(id.clone());
                queue.patch(&id, |entry| {
                    entry.status = JobStatus::Queued;
                    entry.pid = None;
                    entry.stage = Some(crate::job::JobStage::Decrypt);
                    entry.message = "下载完成，正在等待解密和有序合并".to_string();
                });
            } else {
                queue.finish(&id);
            }
            // The switch goes with the process: a forced stop must never reach a run that has
            // already been settled, and its pid could by now belong to something else.
            queue.forget_worker(&id);
        }

        let finished_merges: Vec<String> = merges
            .iter_mut()
            .filter_map(|(id, process)| match process.poll() {
                job::Poll::Working => None,
                job::Poll::Exited { .. } => Some(id.clone()),
            })
            .collect();
        for id in finished_merges {
            let Some(mut process) = merges.remove(&id) else {
                continue;
            };
            process.drain();
            let completed_merge = queue
                .snapshot()
                .items
                .iter()
                .find(|entry| entry.item.id == id)
                .is_some_and(|entry| entry.status == JobStatus::Complete);
            if completed_merge {
                ready_to_publish.insert(id.clone());
                queue.patch(&id, |entry| {
                    entry.status = JobStatus::Queued;
                    entry.pid = None;
                    entry.stage = Some(crate::job::JobStage::Remux);
                    entry.message = "解密和有序合并完成，正在等待封装发布".to_string();
                });
            } else {
                queue.finish(&id);
            }
            queue.forget_worker(&id);
        }

        let finished_publishes: Vec<String> = publishes
            .iter_mut()
            .filter_map(|(id, process)| match process.poll() {
                job::Poll::Working => None,
                job::Poll::Exited { .. } => Some(id.clone()),
            })
            .collect();
        for id in finished_publishes {
            let Some(mut process) = publishes.remove(&id) else {
                continue;
            };
            process.drain();
            queue.finish(&id);
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
            for process in downloads
                .values()
                .chain(merges.values())
                .chain(publishes.values())
            {
                let _ = process.request_stop();
            }
        }

        if queued.is_empty() && downloads.is_empty() && merges.is_empty() && publishes.is_empty() {
            break;
        }

        if !stopping {
            // Each stage has its own `同时导出` pool. A downloading lesson never consumes a
            // merge or publish slot, so completed lessons flow downstream without blocking the
            // network queue (and vice versa).
            let merge_ids: Vec<String> = queued
                .iter()
                .filter(|id| ready_to_merge.contains(*id))
                .take(count.max(1).saturating_sub(merges.len()))
                .cloned()
                .collect();
            for id in merge_ids {
                if let Some(process) = spawn(queue, sink, cli, options, &id, ExportPhase::Merge) {
                    ready_to_merge.remove(&id);
                    merges.insert(id, process);
                }
            }

            let publish_ids: Vec<String> = queued
                .iter()
                .filter(|id| ready_to_publish.contains(*id))
                .take(count.max(1).saturating_sub(publishes.len()))
                .cloned()
                .collect();
            for id in publish_ids {
                if let Some(process) = spawn(queue, sink, cli, options, &id, ExportPhase::Publish) {
                    ready_to_publish.remove(&id);
                    publishes.insert(id, process);
                }
            }

            let download_ids: Vec<String> = queued
                .iter()
                .filter(|id| {
                    !ready_to_merge.contains(*id)
                        && !ready_to_publish.contains(*id)
                        && !merges.contains_key(*id)
                        && !publishes.contains_key(*id)
                        && !downloads.contains_key(*id)
                })
                .take(count.max(1).saturating_sub(downloads.len()))
                .cloned()
                .collect();
            for id in download_ids {
                if let Some(process) = spawn(queue, sink, cli, options, &id, ExportPhase::Download)
                {
                    downloads.insert(id, process);
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

/// Claim and start one stage. A row stays the same user-visible job while its CLI process changes
/// from `download` to `convert`, so stop/force-stop and transcript handling remain uniform.
fn spawn(
    queue: &Queue,
    sink: &Arc<dyn Sink>,
    cli: &str,
    options: &Options,
    id: &str,
    phase: ExportPhase,
) -> Option<Running> {
    match queue.claim(id) {
        Ok(Some(entry)) => {
            let job_sink: Arc<dyn Sink> =
                Arc::new(JobSink::new(queue.clone(), sink.clone(), id.to_string()));
            match job::spawn(cli, &entry.item, options, phase, &job_sink) {
                Ok(process) => {
                    let pid = process.pid;
                    #[cfg(windows)]
                    let switch = KillSwitch::new(id.to_string(), pid, process.job.clone());
                    #[cfg(not(windows))]
                    let switch = KillSwitch::new(id.to_string(), pid);
                    queue.patch(id, |stored| {
                        stored.status = JobStatus::Running;
                        stored.pid = Some(pid);
                        stored.message = "已启动".to_string();
                    });
                    queue.lock().workers.push(switch);
                    Some(process)
                }
                Err(error) => {
                    queue.patch(id, |stored| {
                        stored.status = JobStatus::Failed;
                        stored.message = error;
                    });
                    None
                }
            }
        }
        Ok(None) => None,
        Err(error) => {
            queue.patch(id, |stored| {
                stored.status = JobStatus::Failed;
                stored.message = error;
            });
            None
        }
    }
}
