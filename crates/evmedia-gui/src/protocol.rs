//! Where the CLI's event vocabulary becomes the queue's.
//!
//! `docs/CLI-CONTRACT.md` freezes the JSON lines the CLI writes with `--json-events`; the queue,
//! the window and the tests all speak the shapes in `job`. This module is the single place the two
//! meet, so a renamed event is a compile error here rather than a silently dead progress bar.
//!
//! It also defines who hears about a movement. The window's sink pushes events at the webview, and
//! each running job gets a sink that first writes its own row — the numbers have to reach the queue
//! as well as the screen, or a lesson can finish while the queue still believes it is running.

use crate::{
    job::{JobEntry, JobStage, JobStatus},
    queue::Queue,
};
use evmedia_contract::{Event, Stage as CliStage, StageState, Status as CliStatus};
use std::sync::Arc;

/// Anything that wants to hear about a job as it moves. The Tauri shell implements this to push
/// events at the webview; the tests implement it with a vector.
pub trait Sink: Send + Sync + 'static {
    fn entry(&self, entry: &JobEntry);
    fn log(&self, id: &str, line: &str);
    fn event(&self, _id: &str, _event: &Event) {}
    /// Where the run's full transcript is being written. Non-fatal if it cannot be stored: the
    /// transcript is what a failure is diagnosed from, not what makes the run succeed.
    fn transcript(&self, _id: &str, _path: &str) {}
    /// The whole batch is over. Without this the window would keep showing "exporting" after the
    /// last worker stopped, because nothing else ends the run.
    fn finished(&self) {}
}

/// A sink that drops everything, for a queue with no window attached.
pub struct Silent;

impl Sink for Silent {
    fn entry(&self, _entry: &JobEntry) {}
    fn log(&self, _id: &str, _line: &str) {}
}

/// The sink one running job writes to: it keeps the job's own row current, then passes everything on
/// to whoever is watching.
///
/// The writer is the job's reader thread, never the queue thread, so taking the queue lock here
/// cannot deadlock against the pump.
pub struct JobSink {
    queue: Queue,
    sink: Arc<dyn Sink>,
    id: String,
}

impl JobSink {
    pub fn new(queue: Queue, sink: Arc<dyn Sink>, id: String) -> Self {
        Self { queue, sink, id }
    }
}

impl Sink for JobSink {
    fn entry(&self, entry: &JobEntry) {
        self.sink.entry(entry);
    }

    fn log(&self, id: &str, line: &str) {
        {
            let mut state = self.queue.lock();
            if let Some(entry) = state.items.iter_mut().find(|entry| entry.item.id == self.id) {
                entry.push_log(line);
            }
        }
        self.sink.log(id, line);
    }

    fn transcript(&self, _id: &str, path: &str) {
        let snapshot = {
            let mut state = self.queue.lock();
            let Some(entry) = state.items.iter_mut().find(|entry| entry.item.id == self.id) else {
                return;
            };
            entry.log_path = Some(path.to_string());
            entry.clone()
        };
        self.sink.entry(&snapshot);
    }

    fn event(&self, id: &str, event: &Event) {
        let snapshot = {
            let mut state = self.queue.lock();
            let Some(entry) = state.items.iter_mut().find(|entry| entry.item.id == self.id) else {
                return;
            };
            apply(entry, event);
            // `progress` arrives once per segment and `log` far more often, so the row is only
            // republished when it actually says something new.
            matches!(
                event,
                Event::Started { .. }
                    | Event::Stage { .. }
                    | Event::Log { .. }
                    | Event::Artifact { .. }
                    | Event::Finished { .. }
            )
            .then(|| entry.clone())
        };
        if let Some(snapshot) = snapshot {
            self.sink.entry(&snapshot);
        }
        self.sink.event(id, event);
    }
}

/// Translate one protocol event into the queue's own state.
pub fn apply(entry: &mut JobEntry, event: &Event) {
    match event {
        Event::Started { protocol, .. } => {
            if *protocol != evmedia_contract::event::PROTOCOL_V1 {
                entry.message = format!("CLI 的协议版本是 {protocol}，本窗口只认识 v1");
            }
        }
        Event::Stage { name, state, detail } => {
            if matches!(state, StageState::Begin) {
                entry.stage = Some(match name {
                    CliStage::Scan => JobStage::Preparing,
                    CliStage::Download => JobStage::Download,
                    CliStage::Merge => JobStage::Decrypt,
                    CliStage::Remux => JobStage::Remux,
                });
            }
            if !detail.is_empty() {
                entry.message = detail.clone();
            }
            if matches!(name, CliStage::Download) {
                if let Some(total) = total_from_detail(detail) {
                    entry.progress.total = total;
                }
            }
        }
        Event::Progress { segments, done, failed, elapsed_secs, .. } => {
            entry.progress.done = *done;
            entry.progress.failed = *failed;
            entry.progress.elapsed_secs = *elapsed_secs;
            if *segments > entry.progress.total {
                entry.progress.total = *segments;
            }
        }
        Event::Segment { .. } => {}
        Event::Log { message, .. } => entry.message = message.clone(),
        Event::Artifact { path, .. } => entry.message = format!("产物：{path}"),
        Event::Finished { status, message, .. } => {
            entry.status = match status {
                CliStatus::Complete => JobStatus::Complete,
                CliStatus::Cancelled => JobStatus::Cancelled,
                // `partial` and `nothing` are outcomes the user must not read as success, and
                // `failed` is a failure; all three end the run without a usable file.
                CliStatus::Partial | CliStatus::Nothing | CliStatus::Failed => JobStatus::Failed,
            };
            if !message.is_empty() {
                entry.message = message.clone();
            }
            if matches!(status, CliStatus::Complete) {
                entry.stage = Some(JobStage::Done);
                entry.progress.done = entry.progress.total.max(entry.progress.done);
            }
        }
    }
}

/// `"191/191 segment(s) of 191"` → `191`. The download stage's detail carries the only total the CLI
/// ever states, so it is worth reading rather than showing a bar with no denominator.
fn total_from_detail(detail: &str) -> Option<usize> {
    let after = detail.split("of ").nth(1)?;
    after.split(|c: char| !c.is_ascii_digit()).next()?.parse().ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::plan::JobItem;
    use std::path::PathBuf;

    fn entry() -> JobEntry {
        JobEntry::new(
            JobItem {
                id: "1:2".into(),
                course: 1,
                file: 2,
                title: "t".into(),
                duration_seconds: None,
                path: Vec::new(),
                output: PathBuf::from("o.mp4"),
                work: PathBuf::from("w"),
            },
            vec!["export-evs".into()],
            JobStatus::Running,
            String::new(),
        )
    }

    /// The three statuses that are not success must not become success. `partial` in particular is
    /// what a truncated lesson produces, and it leaves a file on disk that looks finished.
    #[test]
    fn only_complete_is_complete() {
        for (status, expected) in [
            (CliStatus::Complete, JobStatus::Complete),
            (CliStatus::Partial, JobStatus::Failed),
            (CliStatus::Nothing, JobStatus::Failed),
            (CliStatus::Failed, JobStatus::Failed),
            (CliStatus::Cancelled, JobStatus::Cancelled),
        ] {
            let mut job = entry();
            apply(&mut job, &Event::Finished { status, exit_code: 0, message: String::new() });
            assert_eq!(job.status, expected, "{status:?} mapped wrongly");
        }
    }

    #[test]
    fn a_partial_run_says_so_in_its_summary() {
        let mut job = entry();
        apply(&mut job, &Event::Finished {
            status: CliStatus::Partial,
            exit_code: 0,
            message: "merged 176 segment(s)".into(),
        });
        assert!(job.summary().contains("失败"));
        assert!(job.summary().contains("merged 176"));
    }

    #[test]
    fn a_download_total_is_read_from_the_stage_detail() {
        assert_eq!(total_from_detail("0/0 segment(s) of 191"), Some(191));
        assert_eq!(total_from_detail("191/191 segment(s) of 191"), Some(191));
        assert_eq!(total_from_detail("no numbers here"), None);
    }

    #[test]
    fn stages_map_onto_the_windows_vocabulary() {
        let mut job = entry();
        for (name, expected) in [
            (CliStage::Scan, JobStage::Preparing),
            (CliStage::Download, JobStage::Download),
            (CliStage::Merge, JobStage::Decrypt),
            (CliStage::Remux, JobStage::Remux),
        ] {
            apply(&mut job, &Event::Stage { name, state: StageState::Begin, detail: String::new() });
            assert_eq!(job.stage, Some(expected));
        }
    }

    #[test]
    fn progress_sets_the_denominator_and_the_numerator() {
        let mut job = entry();
        apply(&mut job, &Event::Progress {
            stage: CliStage::Download,
            keys: 4,
            urls: 4,
            segments: 191,
            done: 37,
            failed: 1,
            elapsed_secs: 12,
        });
        assert_eq!(job.progress.total, 191);
        assert_eq!(job.progress.done, 37);
        assert_eq!(job.progress.failed, 1);
        assert_eq!(job.progress.elapsed_secs, 12);
    }

    #[test]
    fn a_foreign_protocol_is_reported_rather_than_guessed() {
        let mut job = entry();
        apply(&mut job, &Event::Started { protocol: 99, command: Vec::new(), app_version: "9".into() });
        assert!(job.message.contains("99"));
    }
}
