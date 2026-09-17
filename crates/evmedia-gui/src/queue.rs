//! The export queue: what to export, what is running, and what happened.
//!
//! Concurrency has two levels and the window owns the outer one. `--jobs` parallelises the *segments*
//! of one lesson inside the CLI; the worker count here parallelises whole lessons, so several videos
//! make progress at once. Neither is implemented here — a worker is nothing but a spawned
//! `evmedia export-evs`, and the loop that runs them is `crate::runner`.
//!
//! The state is shared behind one lock and the lock is never held across a wait: the process handles
//! belong to the runner thread alone, so a job that dies is only ever noticed by the one thread that
//! can also stop it.

use crate::job::{JobEntry, JobStatus, Progress};
use crate::plan::Options;
use crate::protocol::Sink;
use crate::runner::{self, KillSwitch};
use serde::{Deserialize, Serialize};
use std::{
    sync::{Arc, Condvar, Mutex, MutexGuard},
    time::Duration,
};

#[derive(Default)]
pub struct State {
    pub items: Vec<JobEntry>,
    /// True while the runner thread is alive.
    pub running: bool,
    /// Set by `stop`; the runner notices it and asks every worker to stop.
    pub stop_requested: bool,
    /// The workers of the running batch, reachable from outside the runner thread so a forced stop
    /// can reach them. Empty between batches.
    pub workers: Vec<KillSwitch>,
    /// A message for the user when the whole batch stopped for one shared reason.
    pub halt: Option<String>,
}

#[derive(Clone)]
pub struct Queue {
    state: Arc<Mutex<State>>,
    changed: Arc<Condvar>,
    sink: Arc<dyn Sink>,
}

/// What the frontend renders. One snapshot per change, rather than making the view reassemble the
/// queue from deltas.
#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct Snapshot {
    pub items: Vec<JobEntry>,
    pub running: bool,
    /// True between a stop request and the last worker leaving, which is what the window needs to
    /// say "正在停止…" instead of looking like the button did nothing.
    pub stopping: bool,
    pub halt: Option<String>,
}

impl Queue {
    pub fn new(sink: Arc<dyn Sink>) -> Self {
        Self {
            state: Arc::new(Mutex::new(State::default())),
            changed: Arc::new(Condvar::new()),
            sink,
        }
    }

    /// The queue's own lock. Public because the runner and a running job's sink both have to write
    /// rows, and both live outside this module.
    pub fn lock(&self) -> MutexGuard<'_, State> {
        // A panic while holding this lock would leave the queue unusable, and the queue is only ever
        // updated with plain field writes, so recovering is the right reading of a poison.
        self.state.lock().unwrap_or_else(|error| error.into_inner())
    }

    pub fn changed(&self) -> &Condvar {
        &self.changed
    }

    pub fn snapshot(&self) -> Snapshot {
        let state = self.lock();
        Snapshot {
            items: state.items.clone(),
            running: state.running,
            stopping: state.running && state.stop_requested,
            halt: state.halt.clone(),
        }
    }

    pub fn is_running(&self) -> bool {
        self.lock().running
    }

    pub fn halt(&self) -> Option<String> {
        self.lock().halt.clone()
    }

    /// Replace the queue with a new batch, before it is started. Anything already running must be
    /// stopped first; the window disables the button while a batch is in flight.
    pub fn set_rows(&self, items: Vec<JobEntry>) {
        let mut state = self.lock();
        state.items = items;
        state.halt = None;
    }

    /// Start workers over the rows already in the queue. Separate from `start`, which takes a new
    /// batch, because the window shows the plan — and lets the user uncheck rows — before running.
    pub fn start_with_existing(&self, options: Options, workers: usize, cli: String) {
        {
            let mut state = self.lock();
            if state.running {
                return;
            }
            state.running = true;
            state.stop_requested = false;
            state.halt = None;
            state.workers.clear();
            for entry in state.items.iter_mut() {
                if entry.status != JobStatus::Queued {
                    entry.status = JobStatus::Queued;
                    entry.message.clear();
                }
                entry.stage = None;
                entry.progress = Progress::default();
            }
        }
        self.changed.notify_all();
        let queue = self.clone();
        let sink = self.sink.clone();
        std::thread::Builder::new()
            .name("evmedia-runner".to_string())
            .spawn(move || runner::run(&queue, &sink, &cli, &options, workers.max(1)))
            .expect("spawn the runner thread");
    }

    /// Start the workers. `items` is the whole batch to run, replacing whatever was there. A running
    /// batch is left alone: two batches sharing one worker pool would make the totals meaningless.
    pub fn start(&self, items: Vec<JobEntry>, options: Options, workers: usize, cli: String) {
        self.set_rows(items);
        self.start_with_existing(options, workers, cli);
    }

    /// Halt the batch for one shared reason, and say what it was. A halt does not discard the rows:
    /// they stay, failed, so the session can be refreshed and the same batch retried.
    pub fn set_halt(&self, message: Option<String>) {
        {
            let mut state = self.lock();
            state.halt = message;
        }
        self.changed.notify_all();
    }

    pub fn clear_finished(&self) {
        {
            let mut state = self.lock();
            state
                .items
                .retain(|entry| matches!(entry.status, JobStatus::Queued | JobStatus::Running));
            // A halt is about the batch that just ended, so clearing the rows clears it too.
            state.halt = None;
        }
        self.changed.notify_all();
    }

    /// Move finished rows back to the queue. A failure is usually transient — an expired token that
    /// has since been refreshed, a flaky segment — so retrying must not need the batch to be rebuilt
    /// by hand. Returns how many rows were requeued.
    pub fn retry_finished(&self) -> usize {
        let count = {
            let mut state = self.lock();
            let mut count = 0;
            for entry in state.items.iter_mut() {
                match entry.status {
                    JobStatus::Failed | JobStatus::Cancelled | JobStatus::Skipped => {
                        entry.status = JobStatus::Queued;
                        entry.message.clear();
                        entry.progress = Progress::default();
                        entry.stage = None;
                        count += 1;
                    }
                    JobStatus::Queued | JobStatus::Running | JobStatus::Complete => {}
                }
            }
            state.halt = None;
            count
        };
        self.changed.notify_all();
        count
    }

    pub fn has_pending(&self) -> bool {
        self.lock().items.iter().any(|entry| entry.status == JobStatus::Queued)
    }

    /// Ask the batch to stop. Cooperative on purpose: the CLI notices at its next batch boundary,
    /// merges what it has, and exits — so a stopped lesson still resumes instead of restarting.
    pub fn stop(&self) {
        {
            let mut state = self.lock();
            state.stop_requested = true;
        }
        self.changed.notify_all();
    }

    /// Kill every running worker now, and everything they started. Returns how many were reached.
    ///
    /// The graceful stop is the one that leaves a resumable lesson behind, and it is what the stop
    /// button asks for. This is the answer to "it is not stopping": a CLI inside a long download or
    /// an ffmpeg pass only notices the stop file at its next boundary, and a user who has waited long
    /// enough deserves a way out that does not involve Task Manager.
    pub fn kill_running(&self) -> usize {
        let switches: Vec<KillSwitch> = self.lock().workers.clone();
        let mut killed = 0;
        for switch in &switches {
            if switch.terminate() {
                killed += 1;
                self.patch(&switch.id, |entry| {
                    entry.message = "已强制结束（当前分段可能不完整，重跑会重新下载它）".to_string();
                });
            }
        }
        // The runner will see each process die and settle its row; stop asking it to wait.
        self.stop();
        killed
    }

    /// Drop the kill switch for a run that has been settled. A forced stop must never reach a run
    /// that is already over: its pid could by then belong to something else.
    pub fn forget_worker(&self, id: &str) {
        self.lock().workers.retain(|switch| switch.id != id);
    }

    /// Claim a queued row, turning "the output is already there" into a skipped row without spawning
    /// anything.
    pub fn claim(&self, id: &str) -> Result<Option<JobEntry>, String> {
        let mut state = self.lock();
        let Some(entry) = state.items.iter_mut().find(|e| e.item.id == id) else {
            return Ok(None);
        };
        if entry.item.output.exists() {
            entry.status = JobStatus::Skipped;
            entry.message = "输出已存在，未重新导出；勾选“覆盖已有文件”可重跑".to_string();
            let snapshot = entry.clone();
            drop(state);
            self.sink.entry(&snapshot);
            self.changed.notify_all();
            return Ok(None);
        }
        Ok(Some(entry.clone()))
    }

    pub fn patch(&self, id: &str, change: impl FnOnce(&mut JobEntry)) {
        let snapshot = {
            let mut state = self.lock();
            let Some(entry) = state.items.iter_mut().find(|e| e.item.id == id) else {
                return;
            };
            change(entry);
            entry.clone()
        };
        self.sink.entry(&snapshot);
        self.changed.notify_all();
    }

    /// Settle a job whose process is gone *and whose output has been fully read*.
    ///
    /// There is deliberately no branch on the exit code here. The only promise that a complete file
    /// was published is the status the CLI itself reported, and a graceful stop exits 0 just like a
    /// success does — so a run that ended without reporting one is a failure, not a silent success.
    pub fn finish(&self, id: &str) {
        self.patch(id, |entry| {
            entry.pid = None;
            if entry.status != JobStatus::Running {
                return;
            }
            entry.status = JobStatus::Failed;
            if entry.message.is_empty() || entry.message == "已启动" {
                entry.message = "CLI 结束了，但没有报告完成状态，完整日志见 work 目录".to_string();
            }
        });
    }

    /// Mark the batch over. Called by the runner, which is the only thing that knows.
    pub fn settle(&self) {
        {
            let mut state = self.lock();
            state.running = false;
            state.stop_requested = false;
            state.workers.clear();
        }
        self.changed.notify_all();
    }

    /// Block until every worker has stopped. For the tests and the headless harness; the window
    /// never waits, it listens.
    pub fn wait_idle(&self, timeout: Duration) -> bool {
        runner::wait_idle(self, timeout)
    }
}

/// Phrases that mean the session itself is no longer usable. Worth matching on strings: a batch that
/// keeps running after the token dies fails once per video for one reason, and the user has to read
/// the last log line to find out which. This lives on the window's side of the boundary on purpose —
/// the runner must never take the queue lock from inside a sink callback, because that callback runs
/// while the lock is already held.
const AUTH_PHRASES: &[&str] = &[
    "其他设备上登录",
    "登录已过期",
    "token expired",
    "Token expired",
    "Unauthorized",
    "unauthorized",
    "invalid token",
    "errcode 401",
    "errcode 403",
];

pub fn looks_like_auth_failure(text: &str) -> bool {
    AUTH_PHRASES.iter().any(|phrase| text.contains(phrase))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_expired_session_is_recognised() {
        for text in [
            "catalog API refused: 1001: 当前账号已经在其他设备上登录",
            "Error: catalog API refused: 401: unauthorized",
            "request failed: invalid token",
            "登录已过期，请重新登录",
        ] {
            assert!(looks_like_auth_failure(text), "{text} should read as an auth failure");
        }
        for text in ["segment 17 checksum mismatch", "ffmpeg remux failed with exit status 1"] {
            assert!(!looks_like_auth_failure(text), "{text} must not halt the batch");
        }
    }
}
