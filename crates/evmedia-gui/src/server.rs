//! The window's command surface: everything the frontend is allowed to ask for.
//!
//! Every one of these is a thin translation. Discovering the CLI, reading a session out of the
//! player, reading a directory, planning an argv and running it are all things `sniff`, `catalog`,
//! `plan` and the queue already do; this file turns them into `invoke()`-able functions and pushes
//! the queue's movements at the webview.

use crate::{
    catalog::Catalog,
    config,
    job::{JobEntry, JobStatus},
    locate, plan,
    protocol::Sink,
    queue, session, sniff,
};
use evmedia_contract::Event;
use serde::{Deserialize, Serialize};
use std::{
    path::PathBuf,
    process::Command,
    sync::{Arc, Mutex},
};
use tauri::{AppHandle, Emitter, Manager, Runtime};

/// The channel the frontend listens on for queue movements.
pub const EVENT_CHANNEL: &str = "queue";

#[derive(Default)]
pub struct AppState {
    /// Built on first use, because the sink needs an `AppHandle` that only exists once Tauri has
    /// started.
    queue: Mutex<Option<queue::Queue>>,
}

/// The sink that turns a queue movement into a webview event. It is deliberately stateless: the
/// queue's lock is held while this runs, so it must not call back into the queue.
struct AppSink<R: Runtime> {
    app: AppHandle<R>,
}

impl<R: Runtime> Sink for AppSink<R> {
    fn entry(&self, entry: &JobEntry) {
        let _ = self.app.emit(EVENT_CHANNEL, Progress::Entry { entry: entry.clone() });
    }

    fn log(&self, id: &str, line: &str) {
        let _ = self.app.emit(
            EVENT_CHANNEL,
            Progress::Log { id: id.to_string(), line: line.to_string() },
        );
    }

    fn event(&self, id: &str, event: &Event) {
        let _ = self.app.emit(
            EVENT_CHANNEL,
            Progress::Protocol { id: id.to_string(), event: event.clone() },
        );
    }

    fn finished(&self) {
        let _ = self.app.emit(EVENT_CHANNEL, Progress::Finished);
    }
}

#[derive(Serialize, Deserialize, Clone)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum Progress {
    Entry { entry: JobEntry },
    Log { id: String, line: String },
    Protocol { id: String, event: Event },
    /// The batch ended: the window refetches the queue and finds `running: false`.
    Finished,
}

#[derive(Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct RefreshOutcome {
    pub courses: usize,
    pub videos: usize,
    pub seconds: f64,
    pub catalog: Catalog,
}

/// What the window needs to know about its own environment before it can do anything: which CLI it
/// drives, whether the media tools exist, and whether there is a session yet.
#[derive(Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct Environment {
    pub cli: locate::CliStatus,
    pub session: Option<session::Summary>,
    pub session_error: Option<String>,
    pub media_tools: bool,
    /// A player with a window is running, which is what "自动获取会话" needs.
    pub player_running: bool,
    /// Why those two came out the way they did. A button that does nothing has to say what it
    /// checked; "no player running" and "could not look for the player" are different problems.
    pub note: String,
}

/// Build the queue once, on first use, so `Queue` needs no `Default` and its sink can hold the app
/// handle. The lock is only ever held for the `Option` check, never while work happens.
fn queue_of<R: Runtime>(app: &AppHandle<R>) -> queue::Queue {
    let state = app.state::<AppState>();
    let mut guard = state.queue.lock().unwrap_or_else(|error| error.into_inner());
    guard
        .get_or_insert_with(|| queue::Queue::new(Arc::new(AppSink { app: app.clone() })))
        .clone()
}

#[tauri::command]
pub fn get_config() -> config::GuiConfig {
    config::load()
}

#[tauri::command]
pub fn set_config(settings: config::GuiConfig) -> Result<(), String> {
    config::save(&settings)
}

/// The CLI's own argument list, for the argv preview the window shows.
#[tauri::command]
pub fn command_spec() -> Vec<evmedia_contract::CommandSpec> {
    evmedia_contract::describe()
}

/// One call that answers "can this window do anything yet". The window used to discover this by
/// failing at the first button press, one missing thing at a time.
#[tauri::command]
pub fn check_environment(settings: config::GuiConfig) -> Environment {
    let settings = settings.normalized();
    let cli = locate::check_cli(settings.cli_path.clone());
    let path = settings.session_path();
    let (summary, error) = if path.is_file() {
        match session::inspect(&path) {
            Ok(summary) => (Some(summary), None),
            Err(error) => (None, Some(error)),
        }
    } else {
        (None, None)
    };
    let player = sniff::find_player();
    let environment = Environment {
        cli,
        session: summary,
        session_error: error,
        media_tools: sniff::media_tools_ok(&settings.ffmpeg, &settings.ffprobe),
        note: match &player {
            Ok(pid) => format!("播放器进程 {pid}"),
            Err(reason) => reason.clone(),
        },
        player_running: player.is_ok(),
    };
    crate::log::info(&format!(
        "environment: cli={} session={} media={} player={} ({})",
        environment.cli.ok,
        environment.session.is_some(),
        environment.media_tools,
        environment.player_running,
        environment.note
    ));
    environment
}

/// Read the session out of the running player and write it beside the export root.
#[tauri::command]
pub async fn sniff_session(settings: config::GuiConfig) -> Result<sniff::Sniffed, String> {
    let settings = settings.normalized();
    if settings.root.trim().is_empty() {
        return Err("先选择导出目录，会话文件会写在它下面".to_string());
    }
    let root = PathBuf::from(settings.root.trim());
    crate::log::info(&format!("sniffing the session into {}", root.display()));
    let outcome = tauri::async_runtime::spawn_blocking(move || sniff::sniff(&root))
        .await
        .map_err(|error| format!("读取会话的任务失败：{error}"))?;
    match &outcome {
        Ok(sniffed) => crate::log::info(&format!(
            "session written to {} from pid {} ({} fields, {:.1}s)",
            sniffed.path,
            sniffed.pid,
            sniffed.fields.len(),
            sniffed.seconds
        )),
        Err(error) => crate::log::warn(&format!("sniff failed: {error}")),
    }
    outcome
}

#[tauri::command]
pub fn open_path(path: String) -> Result<(), String> {
    let target = if PathBuf::from(&path).is_dir() {
        path
    } else {
        PathBuf::from(&path)
            .parent()
            .map(|parent| parent.display().to_string())
            .unwrap_or(path)
    };
    #[cfg(windows)]
    {
        Command::new("explorer")
            .arg(&target)
            .spawn()
            .map_err(|error| format!("打开 {target} 失败：{error}"))?;
    }
    #[cfg(not(windows))]
    {
        let opener = if cfg!(target_os = "macos") { "open" } else { "xdg-open" };
        Command::new(opener)
            .arg(&target)
            .spawn()
            .map_err(|error| format!("打开 {target} 失败：{error}"))?;
    }
    Ok(())
}

#[tauri::command]
pub fn inspect_session(path: String) -> Result<session::Summary, String> {
    session::inspect(std::path::Path::new(&path))
}

/// Let the frontend put a line in the log. A window has no console, and "the button did nothing"
/// is not a bug report anyone can act on.
#[tauri::command]
pub fn log_note(message: String) {
    crate::log::info(&message);
}

/// Where the log is, so the window can offer to open it.
#[tauri::command]
pub fn log_path() -> String {
    crate::log::path().display().to_string()
}

#[tauri::command]
pub fn load_catalog(root: String) -> Result<Option<Catalog>, String> {
    crate::refresh::catalog_at(&root)
}

/// `evmedia catalog`: walk every authorized course and write the tree.
#[tauri::command]
pub async fn refresh_catalog(
    app: AppHandle<impl Runtime>,
    cli: String,
    session: String,
    account: i64,
    root: String,
) -> Result<crate::refresh::RefreshOutcome, String> {
    crate::refresh::run(app, cli, session, account, root).await
}

/// Turn a selection into the queue's rows without starting anything, so the window can show what a
/// run will do — including which videos it will skip — before it commits.
#[tauri::command]
pub fn preview_export(settings: config::GuiConfig, ids: Vec<String>) -> Result<Vec<JobEntry>, String> {
    let catalog = crate::refresh::catalog_at(&settings.root)?
        .ok_or_else(|| "还没有课程目录，请先刷新目录".to_string())?;
    let found = catalog.selected(&ids);
    if found.is_empty() {
        return Err("没有选中任何视频".to_string());
    }
    let options = settings.options()?;
    Ok(found
        .into_iter()
        .map(|video| {
            let item = plan::item_of(&options, &video);
            let argv = plan::argv(&item, &options);
            let (status, message) = if item.output.exists() {
                (JobStatus::Skipped, "输出已存在，不会重新导出".to_string())
            } else {
                (JobStatus::Queued, String::new())
            };
            JobEntry::new(item, argv, status, message)
        })
        .collect())
}

#[tauri::command]
pub fn enqueue_export(
    app: AppHandle<impl Runtime>,
    settings: config::GuiConfig,
    ids: Vec<String>,
) -> Result<Vec<JobEntry>, String> {
    let rows = preview_export(settings, ids)?;
    let queue = queue_of(&app);
    for row in &rows {
        let _ = app.emit(EVENT_CHANNEL, Progress::Entry { entry: row.clone() });
    }
    queue.set_rows(rows.clone());
    Ok(rows)
}

#[tauri::command]
pub fn start_export(
    app: AppHandle<impl Runtime>,
    settings: config::GuiConfig,
) -> Result<queue::Snapshot, String> {
    let queue = queue_of(&app);
    if queue.is_running() {
        return Err("已经在导出中".to_string());
    }
    if !queue.has_pending() {
        return Err("队列里没有待导出的视频".to_string());
    }
    let cli = locate::resolve(settings.cli_path.as_deref())?.0.display().to_string();
    let options = settings.options()?;
    let workers = settings.workers.clamp(1, 16);
    let snapshot = queue.snapshot();
    queue.start_with_existing(options, workers, cli);
    Ok(snapshot)
}

#[tauri::command]
pub fn stop_export(app: AppHandle<impl Runtime>) -> Result<(), String> {
    let queue = queue_of(&app);
    if !queue.is_running() {
        return Err("当前没有正在运行的导出".to_string());
    }
    queue.stop();
    crate::log::info("stop requested; the CLI merges what it has and exits");
    Ok(())
}

/// Stop now: kill the CLI processes, and the ffmpeg they spawned with them.
///
/// The graceful stop is correct and is what the button asks for, but it can only be noticed at the
/// CLI's own boundaries — a long download or an ffmpeg pass will finish first. This is the way out
/// that does not involve Task Manager.
#[tauri::command]
pub fn force_stop(app: AppHandle<impl Runtime>) -> Result<usize, String> {
    let queue = queue_of(&app);
    if !queue.is_running() {
        return Err("当前没有正在运行的导出".to_string());
    }
    let killed = queue.kill_running();
    crate::log::warn(&format!("forced stop: {killed} worker(s) terminated"));
    Ok(killed)
}

#[tauri::command]
pub fn clear_finished(app: AppHandle<impl Runtime>) {
    queue_of(&app).clear_finished();
}

#[tauri::command]
pub fn retry_finished(app: AppHandle<impl Runtime>) -> Result<usize, String> {
    let queue = queue_of(&app);
    if queue.is_running() {
        return Err("先停止当前导出，再重试".to_string());
    }
    Ok(queue.retry_finished())
}

/// The whole batch is failing for one shared reason (an expired session is the usual one). The
/// window decides that from the messages it has seen; this is where it says so.
#[tauri::command]
pub fn set_halt(app: AppHandle<impl Runtime>, message: Option<String>) {
    queue_of(&app).set_halt(message);
}

#[tauri::command]
pub fn queue_snapshot(app: AppHandle<impl Runtime>) -> queue::Snapshot {
    queue_of(&app).snapshot()
}

#[tauri::command]
pub fn job_log(app: AppHandle<impl Runtime>, id: String) -> Vec<String> {
    queue_of(&app)
        .snapshot()
        .items
        .into_iter()
        .find(|entry| entry.item.id == id)
        .map(|entry| entry.log)
        .unwrap_or_default()
}
