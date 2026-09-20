//! One CLI run: build the argv, spawn it, and keep what it says.
//!
//! Everything here is deliberate about whose job each step is. The argv comes from
//! `evmedia-contract`, so the CLI accepts it; the process *is* the CLI, so the download, the key
//! derivation and the decryption are the same code the command line runs; and the JSON lines are
//! the frozen event protocol in `docs/CLI-CONTRACT.md`. This module decides nothing about media —
//! turning those lines into a row's state is `crate::protocol`.

use crate::plan::{self, JobItem, Options};
use crate::protocol::Sink;
use evmedia_contract::{Event, ExportPhase};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{Arc, Condvar, Mutex},
    time::Duration,
};

/// What the user sees for one requested export.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum JobStatus {
    /// Waiting for a worker slot.
    Queued,
    Running,
    Complete,
    Failed,
    /// Stopped by request; whatever was downloaded is kept and a rerun resumes it.
    Cancelled,
    /// The output was already on disk and `--force` was off, so nothing ran.
    Skipped,
}

#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum JobStage {
    Preparing,
    Download,
    Decrypt,
    Remux,
    Done,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
#[serde(rename_all = "camelCase")]
pub struct Progress {
    /// Segments the CLI has reported done.
    pub done: usize,
    /// Segments the CLI expects to handle, once it has said.
    pub total: usize,
    pub failed: usize,
    pub elapsed_secs: u64,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct JobEntry {
    pub item: JobItem,
    pub argv: Vec<String>,
    pub status: JobStatus,
    pub stage: Option<JobStage>,
    pub progress: Progress,
    pub message: String,
    pub pid: Option<u32>,
    pub log: Vec<String>,
    /// The lesson's full transcript on disk.
    ///
    /// `log` is capped because it is cloned into every UI update, and a lesson with 191 segments
    /// produces thousands of lines. The cap used to be the only copy, which meant the diagnostic
    /// that explains a failure could be the part that was dropped. The file is the record; the
    /// vector is the tail.
    #[serde(default)]
    pub log_path: Option<String>,
}

/// Keeps a run's transcript bounded. The tail is what matters when something fails, and the log
/// exists to be read, not to grow until the window runs out of memory.
pub const MAX_LOG_LINES: usize = 400;

/// The lesson's full transcript, inside its own `--work` directory.
pub const STDERR_LOG: &str = "stderr.log";

/// Appends a run's lines to a file as they arrive.
///
/// Held open for the life of the job, so a lesson that prints thousands of lines costs one handle
/// rather than one open per line. A failure to write is not fatal: the run's outcome does not depend
/// on its transcript, and losing a log is not worth failing an export over.
struct Transcript(Option<Mutex<fs::File>>);

impl Transcript {
    /// A new download replaces an old attempt; its conversion is the second half of that same
    /// attempt and must therefore append to the transcript.
    fn create(path: &PathBuf, append: bool) -> Self {
        let file = if append {
            fs::OpenOptions::new().create(true).append(true).open(path)
        } else {
            fs::File::create(path)
        };
        Self(file.ok().map(Mutex::new))
    }

    fn write(&self, line: &str) {
        if let Some(file) = &self.0 {
            if let Ok(mut file) = file.lock() {
                let _ = writeln!(file, "{line}");
            }
        }
    }
}

impl JobEntry {
    pub fn new(item: JobItem, argv: Vec<String>, status: JobStatus, message: String) -> Self {
        Self {
            item,
            argv,
            status,
            stage: None,
            progress: Progress::default(),
            message,
            pid: None,
            log: Vec::new(),
            log_path: None,
        }
    }

    pub fn push_log(&mut self, line: &str) {
        if self.log.len() >= MAX_LOG_LINES {
            self.log.remove(0);
        }
        self.log.push(line.to_string());
    }

    /// A short line for the queue row: the best description of where this job currently is.
    pub fn summary(&self) -> String {
        match self.status {
            JobStatus::Queued => "排队中".to_string(),
            JobStatus::Running => match self.progress.total {
                0 => "正在准备…".to_string(),
                total => format!("已下载 {}/{total} 段", self.progress.done),
            },
            JobStatus::Complete => "完成".to_string(),
            JobStatus::Failed => {
                if self.message.is_empty() {
                    "失败".to_string()
                } else {
                    format!("失败：{}", self.message)
                }
            }
            JobStatus::Cancelled => "已取消，已下载的分段保留，重跑会续传".to_string(),
            JobStatus::Skipped => "已跳过，输出文件已存在".to_string(),
        }
    }
}

/// A spawned run: the child to poll, the readers draining its pipes, and where to write the stop
/// marker.
///
/// The child is *moved* to the queue thread and never shared, so no mutex is ever held across a
/// wait and a process that died is only ever noticed by the thread that can also stop it.
pub struct Running {
    pub child: Child,
    pub pid: u32,
    pub stop_file: PathBuf,
    readers: Arc<Readers>,
    /// A job object holding the CLI and everything it spawns, so "stop now" reaches ffmpeg too.
    #[cfg(windows)]
    pub(crate) job: Option<Arc<crate::winjob::JobHandle>>,
}

/// The readers of a child's stdout and stderr.
///
/// Draining them is not optional. A process exits before the parent has necessarily read the last
/// line it wrote, and that last line is the `finished` event — the one thing that says whether a
/// complete lesson was published. Treating "the process is gone" as "the job is settled" reads a
/// finished export as a silent failure.
#[derive(Default)]
struct Readers {
    open: Mutex<usize>,
    quiet: Condvar,
}

impl Readers {
    fn opened(&self) {
        *self.open.lock().unwrap() += 1;
    }

    fn closed(&self) {
        let mut open = self.open.lock().unwrap();
        *open = open.saturating_sub(1);
        self.quiet.notify_all();
    }

    fn drain(&self) {
        let mut open = self.open.lock().unwrap();
        while *open > 0 {
            let (guard, timeout) = self
                .quiet
                .wait_timeout(open, Duration::from_secs(5))
                .unwrap_or_else(|error| error.into_inner());
            open = guard;
            if timeout.timed_out() && *open > 0 {
                // A pipe held open by a grandchild (ffmpeg, say). Stop waiting: the process itself
                // is gone, and the queue must not stall behind it.
                break;
            }
        }
    }
}

/// What the queue thread learned from one non-blocking look at a running process.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Poll {
    Working,
    /// The process is gone. `code` is `None` when it was killed rather than exiting.
    Exited { code: Option<i32> },
}

impl Running {
    pub fn poll(&mut self) -> Poll {
        match self.child.try_wait() {
            Ok(Some(status)) => Poll::Exited { code: status.code() },
            Ok(None) => Poll::Working,
            // A process this handle cannot wait on is as good as gone.
            Err(_) => Poll::Exited { code: None },
        }
    }

    /// Wait for the last line the process wrote to reach its sink. Call this *before* acting on the
    /// fact that the process is gone.
    pub fn drain(&mut self) {
        self.readers.drain();
    }

    /// Ask the CLI to stop. It notices the file at every batch boundary and inside its own sleep,
    /// then merges what it has and exits 0 — so this is safe to call while a segment is in flight.
    pub fn request_stop(&self) -> Result<(), String> {
        if let Some(parent) = self.stop_file.parent() {
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        fs::write(&self.stop_file, b"").map_err(|error| format!("写入停止标记失败：{error}"))
    }

    /// Kill the CLI itself. Only for "stop now": the CLI may be running ffmpeg, which this does not
    /// reach, so the graceful stop is the one that leaves nothing behind.
    pub fn kill(&mut self) -> Result<(), String> {
        self.child.kill().map_err(|error| format!("结束进程失败：{error}"))
    }
}

/// Build and spawn the CLI for one item, streaming both of its channels into `sink`.
pub fn spawn(
    cli: &str,
    item: &JobItem,
    options: &Options,
    phase: ExportPhase,
    sink: &Arc<dyn Sink>,
) -> Result<Running, String> {
    let argv = plan::argv_for_phase(item, options, phase);
    // The window never invents a command line: the CLI's own parser gets the last word.
    evmedia_contract::try_parse(&argv)?;

    // The child's working directory is the lesson's `--work`, so that directory has to exist before
    // it can be started in — creating only its parent is the difference between a queue that runs
    // and one where every row fails to spawn.
    fs::create_dir_all(&item.work).map_err(|error| format!("创建工作目录失败：{error}"))?;
    if let Some(parent) = &item.output.parent() {
        fs::create_dir_all(parent).map_err(|error| format!("创建输出目录失败：{error}"))?;
    }
    let stop_file = item.work.join(".evmedia-stop");
    // A stop file left over from a previous run would end this one before it started.
    let _ = fs::remove_file(&stop_file);

    let id = item.id.clone();
    let mut command = Command::new(cli);
    command
        .args(argv.iter().map(String::as_str))
        .arg("--json-events")
        .arg("--stop-file")
        .arg(&stop_file)
        .current_dir(&item.work)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if options.stub_tail_ms > 0 {
        command.env("EVSTUB_TAIL_MS", options.stub_tail_ms.to_string());
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }

    let mut child = command.spawn().map_err(|error| format!("启动 {cli} 失败：{error}"))?;
    let pid = child.id();

    // A job object around the CLI, so a forced stop reaches the ffmpeg it spawns partway through a
    // merge. `Child::kill` alone would leave the encoder running and holding the output file.
    #[cfg(windows)]
    let job = crate::winjob::JobHandle::create()
        .filter(|job| job.assign(&child))
        .map(Arc::new);

    // The whole transcript, on disk, from the first line. The in-memory log is capped and is only
    // the tail of this.
    let log_path = item.work.join(STDERR_LOG);
    sink.transcript(&id, &log_path.display().to_string());
    sink.log(&id, &format!("$ {cli} {}", argv.join(" ")));
    sink.log(&id, &format!("完整日志：{}", log_path.display()));
    let transcript = Arc::new(Transcript::create(&log_path, phase == ExportPhase::Convert));

    let readers = Arc::new(Readers::default());
    if let Some(stdout) = child.stdout.take() {
        let sink = sink.clone();
        let id = id.clone();
        let transcript = transcript.clone();
        readers.opened();
        pipe("evmedia-stdout", stdout, readers.clone(), move |line| {
            transcript.write(&line);
            // A line this build cannot parse is text, not an error: that is what lets a newer CLI
            // add event variants without breaking an older window.
            match serde_json::from_str::<Event>(&line) {
                Ok(event) => sink.event(&id, &event),
                Err(_) => sink.log(&id, &line),
            }
        });
    }
    if let Some(stderr) = child.stderr.take() {
        let sink = sink.clone();
        let id = id.clone();
        let transcript = transcript.clone();
        readers.opened();
        pipe("evmedia-stderr", stderr, readers.clone(), move |line| {
            transcript.write(&line);
            sink.log(&id, &line);
        });
    }

    // One non-blocking look per tick, taken by the queue thread itself rather than by a thread per
    // run: `try_wait` cannot block, so there is nothing to gain from a second thread and a second
    // copy of the process handle.
    Ok(Running {
        child,
        pid,
        stop_file,
        readers,
        #[cfg(windows)]
        job,
    })
}

/// Read `reader` line by line on a new thread, handing each line to `on_line`.
fn pipe<R: std::io::Read + Send + 'static>(
    name: &str,
    reader: R,
    readers: Arc<Readers>,
    on_line: impl Fn(String) + Send + 'static,
) {
    let _ = std::thread::Builder::new().name(name.to_string()).spawn(move || {
        for line in BufReader::new(reader).lines().map_while(Result::ok) {
            on_line(line);
        }
        readers.closed();
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(status: JobStatus) -> JobEntry {
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
            status,
            String::new(),
        )
    }

    #[test]
    fn the_log_keeps_its_tail_rather_than_growing_without_bound() {
        let mut job = entry(JobStatus::Running);
        for index in 0..(MAX_LOG_LINES + 50) {
            job.push_log(&format!("line {index}"));
        }
        assert_eq!(job.log.len(), MAX_LOG_LINES);
        assert!(job.log.last().unwrap().ends_with(&format!("{}", MAX_LOG_LINES + 49)));
    }

    /// A skipped row has to say why: "nothing happened" and "this was already done" look the same
    /// on screen otherwise, and one of them is a reason to check the output directory.
    #[test]
    fn a_skipped_row_explains_itself() {
        assert!(entry(JobStatus::Skipped).summary().contains("已跳过"));
    }
}
