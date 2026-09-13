//! Starting, streaming and cancelling one CLI run.
//!
//! The GUI's only mechanism is `std::process::Command` on the `evmedia` binary. Everything the
//! user sees in the progress bar comes from parsing that process's stdout; everything in the
//! log pane is its stderr. There is no shared state, no IPC and no second implementation of
//! any behaviour.

use evmedia_contract::Event;
use serde::Serialize;
use std::{
    io::{BufRead, BufReader},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::Duration,
};
use tauri::{AppHandle, Emitter, Manager};

/// The channel the frontend listens on.
pub const EVENT_CHANNEL: &str = "job";

#[derive(Serialize, Clone)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum Payload {
    /// A parsed protocol event.
    Event { event: Event },
    /// A line of human output (which the CLI sends to stderr in JSON mode), or a stdout line
    /// this build could not parse. Both are shown verbatim.
    Log { line: String },
    /// The process finished.
    Exit { code: i32 },
}

struct Running {
    child: Child,
    stop: PathBuf,
    #[cfg(windows)]
    job: Option<crate::winjob::JobHandle>,
}

#[derive(Default)]
pub struct JobState {
    running: Mutex<Option<Running>>,
}

impl JobState {
    pub fn is_running(&self) -> bool {
        self.running.lock().map(|guard| guard.is_some()).unwrap_or(false)
    }
}

/// Build the argv the CLI will actually receive, so the UI can show it and the user can copy it.
pub fn full_argv(argv: &[String], stop_file: &Path) -> Vec<String> {
    let mut full = argv.to_vec();
    full.push("--json-events".to_string());
    full.push("--stop-file".to_string());
    full.push(stop_file.display().to_string());
    full
}

#[tauri::command]
pub fn start_job(
    app: AppHandle,
    state: tauri::State<JobState>,
    cli: String,
    argv: Vec<String>,
    workdir: String,
) -> Result<(), String> {
    // The GUI never invents a command line: it asks the CLI's own parser whether this is legal,
    // and refuses to spawn otherwise.
    if let Err(error) = evmedia_contract::try_parse(&argv) {
        return Err(error);
    }
    if argv.is_empty() {
        return Err("没有选择要执行的子命令".to_string());
    }

    let mut guard = state.running.lock().map_err(|_| "状态锁异常".to_string())?;
    if guard.is_some() {
        return Err("已有任务在运行，请先等它结束或取消".to_string());
    }

    let workdir = PathBuf::from(&workdir);
    std::fs::create_dir_all(&workdir).map_err(|error| format!("创建输出目录失败：{error}"))?;
    let stop = workdir.join(".evmedia-stop");
    // A leftover stop file from a previous run would end this one immediately.
    let _ = std::fs::remove_file(&stop);

    let mut command = Command::new(&cli);
    command
        .args(full_argv(&argv, &stop))
        .current_dir(&workdir)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }

    let mut child = command.spawn().map_err(|error| format!("启动 {cli} 失败：{error}"))?;

    // A job object so a cancel also reaches ffmpeg, which the CLI spawns partway through a
    // merge. If the process is already in a job that forbids nesting this returns false and we
    // fall back to killing just the child.
    #[cfg(windows)]
    let job = crate::winjob::JobHandle::create().filter(|job| job.assign(&child));

    if let Some(stdout) = child.stdout.take() {
        let app = app.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(|line| line.ok()) {
                // A line this build cannot parse is treated as text, not as an error, so a newer
                // CLI can add event variants without breaking an older GUI.
                let payload = match serde_json::from_str::<Event>(&line) {
                    Ok(event) => Payload::Event { event },
                    Err(_) => Payload::Log { line },
                };
                let _ = app.emit(EVENT_CHANNEL, payload);
            }
        });
    }
    if let Some(stderr) = child.stderr.take() {
        let app = app.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(|line| line.ok()) {
                let _ = app.emit(EVENT_CHANNEL, Payload::Log { line });
            }
        });
    }

    *guard = Some(Running {
        child,
        stop,
        #[cfg(windows)]
        job,
    });
    drop(guard);

    // Reap the process on a timer rather than blocking on `wait`, because the child has to stay
    // reachable so `cancel_job` can signal it.
    let app = app.clone();
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_millis(200));
        let state = app.state::<JobState>();
        let mut guard = match state.running.lock() {
            Ok(guard) => guard,
            Err(_) => return,
        };
        let Some(running) = guard.as_mut() else { return };
        match running.child.try_wait() {
            Ok(Some(status)) => {
                let code = status.code().unwrap_or(-1);
                *guard = None;
                drop(guard);
                let _ = app.emit(EVENT_CHANNEL, Payload::Exit { code });
                return;
            }
            Ok(None) => {}
            Err(_) => {
                *guard = None;
                drop(guard);
                let _ = app.emit(EVENT_CHANNEL, Payload::Exit { code: -1 });
                return;
            }
        }
    });

    Ok(())
}

/// Ask the CLI to stop. It finishes the segment in flight, merges what it has, and exits 0 —
/// so nothing already downloaded is lost.
#[tauri::command]
pub fn cancel_job(app: AppHandle, state: tauri::State<JobState>) -> Result<(), String> {
    let guard = state.running.lock().map_err(|_| "状态锁异常".to_string())?;
    let Some(running) = guard.as_ref() else {
        return Err("没有正在运行的任务".to_string());
    };
    std::fs::write(&running.stop, b"").map_err(|error| format!("写入停止标记失败：{error}"))?;
    let _ = app.emit(EVENT_CHANNEL, Payload::Log {
        line: "已请求停止；正在等待当前分片完成…".to_string(),
    });
    Ok(())
}

/// Give up waiting: kill the whole tree now.
#[tauri::command]
pub fn force_kill(app: AppHandle, state: tauri::State<JobState>) -> Result<(), String> {
    let mut guard = state.running.lock().map_err(|_| "状态锁异常".to_string())?;
    let Some(mut running) = guard.take() else {
        return Err("没有正在运行的任务".to_string());
    };
    #[cfg(windows)]
    if let Some(job) = &running.job {
        job.terminate();
    }
    let _ = running.child.kill();
    drop(guard);
    let _ = app.emit(EVENT_CHANNEL, Payload::Log {
        line: "已强制结束（进程树）".to_string(),
    });
    let _ = app.emit(EVENT_CHANNEL, Payload::Exit { code: -1 });
    Ok(())
}

#[tauri::command]
pub fn job_running(state: tauri::State<JobState>) -> bool {
    state.is_running()
}
