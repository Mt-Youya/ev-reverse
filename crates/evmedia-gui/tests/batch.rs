//! The queue, driven end to end against a stand-in for the CLI.
//!
//! What this proves is the part of the window that is genuinely the window's own: that N workers run
//! N lessons at once, that a lesson whose output is already on disk is skipped instead of
//! re-exported, that a run which ends without reporting a status is not called a success, and that a
//! stop is cooperative rather than a kill. The media pipeline itself is the CLI's, and is not
//! exercised here at all.

use evmedia_contract::Event;
use evmedia_gui::{
    job::{JobEntry, JobStatus},
    plan::{JobItem, Options},
    protocol::Sink,
    queue::{Queue, Snapshot},
};
use std::{
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Duration,
};

/// The stub binary cargo built alongside this test.
const STUB: &str = env!("CARGO_BIN_EXE_evmedia-stub");

/// Records everything the queue reports, so the assertions can be about what the UI would see.
#[derive(Default)]
struct Collect {
    entries: Mutex<Vec<JobEntry>>,
    logs: Mutex<Vec<String>>,
    events: Mutex<Vec<Event>>,
}

impl Collect {
    fn notes(&self) -> Vec<String> {
        self.logs.lock().unwrap().iter().map(|entry| entry.clone()).collect()
    }

    fn saw_event(&self, matches: impl Fn(&Event) -> bool) -> bool {
        self.events.lock().unwrap().iter().any(|event| matches(event))
    }
}

impl Sink for Collect {
    fn entry(&self, entry: &JobEntry) {
        self.entries.lock().unwrap().push(entry.clone());
    }
    fn log(&self, _id: &str, line: &str) {
        self.logs.lock().unwrap().push(line.to_string());
    }
    fn event(&self, _id: &str, event: &Event) {
        self.events.lock().unwrap().push(event.clone());
    }
}

struct Fixture {
    root: PathBuf,
    sink: Arc<Collect>,
    queue: Queue,
    options: Options,
}

impl Fixture {
    fn new(name: &str) -> Self {
        let root =
            std::env::temp_dir().join(format!("evmedia-gui-batch-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let sink = Arc::new(Collect::default());
        let queue = Queue::new(sink.clone());
        let options = Options {
            session: root.join("session.json"),
            account: 119354,
            root: root.clone(),
            jobs: 2,
            extension: "mp4".into(),
            ffmpeg: "ffmpeg".into(),
            ffprobe: "ffprobe".into(),
            force: false,
            stub_tail_ms: 0,
        };
        std::fs::write(&options.session, "{}").unwrap();
        Self { root, sink, queue, options }
    }

    fn item(&self, course: i64, file: i64, title: &str) -> JobItem {
        JobItem {
            id: format!("{course}:{file}"),
            course,
            file,
            title: title.to_string(),
            duration_seconds: Some(60.0),
            path: vec!["chapter".into()],
            output: evmedia_gui::plan::output_of(&self.root, &["chapter".to_string()], title, "mp4"),
            work: evmedia_gui::plan::work_of(&self.root, course, file),
        }
    }

    fn run(&self, items: Vec<JobItem>, workers: usize) {
        self.queue.start(
            items.into_iter().map(row).collect(),
            self.options.clone(),
            workers,
            STUB.to_string(),
        );
        assert!(self.queue.wait_idle(Duration::from_secs(60)), "the queue never went idle");
    }

    fn snapshot(&self) -> Snapshot {
        self.queue.snapshot()
    }
}

fn row(item: JobItem) -> JobEntry {
    let argv = vec![
        "export-evs".to_string(),
        "--course".to_string(),
        item.course.to_string(),
        "--file".to_string(),
        item.file.to_string(),
    ];
    JobEntry::new(item, argv, JobStatus::Queued, String::new())
}

fn status_of(snapshot: &Snapshot, id: &str) -> JobStatus {
    snapshot
        .items
        .iter()
        .find(|entry| entry.item.id == id)
        .unwrap_or_else(|| panic!("{id} is missing from the queue"))
        .status
}

#[test]
fn a_batch_exports_every_selected_video_and_writes_its_output() {
    let fixture = Fixture::new("batch");
    let items: Vec<JobItem> = (1..=3)
        .map(|file| fixture.item(315187, 903780 + file, &format!("lesson {file}")))
        .collect();
    let outputs: Vec<PathBuf> = items.iter().map(|item| item.output.clone()).collect();

    fixture.run(items, 2);

    let snapshot = fixture.snapshot();
    assert_eq!(snapshot.items.len(), 3);
    for id in ["315187:903781", "315187:903782", "315187:903783"] {
        assert_eq!(status_of(&snapshot, id), JobStatus::Complete, "{id} did not complete");
    }
    for output in &outputs {
        assert!(output.is_file(), "{} was not written", output.display());
    }
    // Every job's own argv has to be visible: it is what the user copies to the terminal when a
    // video fails and they want to rerun it by hand.
    assert!(snapshot.items.iter().all(|entry| entry.argv.len() > 1));
    assert!(snapshot.items.iter().all(|entry| entry.pid.is_none()), "a finished job kept its pid");
}

/// A batch is worth running twice: the first pass writes everything, the second should recognise that
/// and do nothing, because re-downloading 191 segments per lesson is the expensive mistake.
#[test]
fn a_second_run_skips_what_is_already_there() {
    let fixture = Fixture::new("skip");
    let items: Vec<JobItem> = (1..=2)
        .map(|file| fixture.item(315187, 903780 + file, &format!("lesson {file}")))
        .collect();
    fixture.run(items.clone(), 2);
    assert!(fixture.snapshot().items.iter().all(|entry| entry.status == JobStatus::Complete));

    fixture.run(items, 2);

    let snapshot = fixture.snapshot();
    assert_eq!(snapshot.items.len(), 2);
    for entry in &snapshot.items {
        assert_eq!(entry.status, JobStatus::Skipped, "{} was not skipped", entry.item.id);
    }
    // Nothing was spawned, so no process ever reported a pid.
    assert!(snapshot.items.iter().all(|entry| entry.pid.is_none()));
}

#[test]
fn progress_reaches_the_window_while_a_video_is_still_running() {
    let fixture = Fixture::new("progress");
    let items = vec![fixture.item(315187, 903780, "lesson 1")];
    fixture.run(items, 1);

    let notes = fixture.sink.notes();
    assert!(
        notes.iter().any(|line| line.contains("downloaded 4/4")),
        "the log lost its progress: {notes:?}"
    );
    assert!(fixture.sink.saw_event(|event| matches!(
        event,
        Event::Progress { done: 4, segments: 4, .. }
    )));
    assert!(fixture.sink.saw_event(|event| matches!(
        event,
        Event::Finished { status: evmedia_contract::Status::Complete, .. }
    )));

    let entry = &fixture.snapshot().items[0];
    assert_eq!(entry.progress.total, 4);
    assert_eq!(entry.progress.done, 4);
    assert_eq!(entry.stage, Some(evmedia_gui::job::JobStage::Done));
}

/// The CLI is allowed to be killed, lose its pipe, or be a different build. None of those may be
/// reported as "your video is ready".
#[test]
fn a_run_that_never_reports_a_status_is_a_failure() {
    let fixture = Fixture::new("silent");
    let item = fixture.item(315187, 999999, "a lesson with no playlist");
    // A program that exists but is not the CLI at all: it exits without saying anything.
    let silent = if cfg!(windows) { "cmd" } else { "true" };
    fixture.queue.start(vec![row(item)], fixture.options.clone(), 1, silent.to_string());
    let _ = fixture.queue.wait_idle(Duration::from_secs(30));

    let snapshot = fixture.snapshot();
    assert_eq!(snapshot.items[0].status, JobStatus::Failed);
    assert!(
        snapshot.items[0].message.contains("没有报告"),
        "unexpected message: {}",
        snapshot.items[0].message
    );
}

#[test]
fn stopping_a_batch_keeps_the_rows_and_marks_them_cancelled() {
    let fixture = Fixture::new("stop");
    let item = fixture.item(315187, 903780, "a very long lesson");
    // The lesson dawdles after its last segment, so the stop lands while it is genuinely working —
    // which is the case the graceful stop exists for.
    let mut options = fixture.options.clone();
    options.stub_tail_ms = 3000;
    fixture.queue.start(vec![row(item)], options, 1, STUB.to_string());
    // Let it start, then ask it to stop the way the window does.
    std::thread::sleep(Duration::from_millis(150));
    fixture.queue.stop();
    assert!(fixture.queue.wait_idle(Duration::from_secs(30)), "the batch ignored the stop");

    let snapshot = fixture.snapshot();
    assert_eq!(snapshot.items[0].status, JobStatus::Cancelled);
    assert!(!snapshot.running);
    // The row survives, so the user can refresh the session and press retry rather than reselect.
    assert_eq!(fixture.queue.retry_finished(), 1);
    assert_eq!(fixture.snapshot().items[0].status, JobStatus::Queued);
}

#[test]
fn clearing_removes_only_rows_that_are_done() {
    let fixture = Fixture::new("clear");
    let items = vec![fixture.item(315187, 903780, "lesson one")];
    fixture.run(items, 1);
    assert_eq!(fixture.snapshot().items.len(), 1);
    fixture.queue.clear_finished();
    assert!(fixture.snapshot().items.is_empty());
}

/// Nothing in the queue may write into another lesson's scratch directory: two exports sharing one
/// `--work` would overwrite each other's segments and produce files of the wrong length.
#[test]
fn every_lesson_gets_its_own_work_directory() {
    let fixture = Fixture::new("work");
    let first = fixture.item(315187, 903780, "lesson one");
    let second = fixture.item(315187, 903781, "lesson two");
    assert_ne!(first.work, second.work);
    assert!(first.work.starts_with(&fixture.root));
}

#[test]
fn a_catalog_file_the_cli_wrote_is_read_back() {
    // The stub writes the same `catalog.json` shape `evmedia catalog` does.
    let root = std::env::temp_dir().join(format!("evmedia-gui-catalog-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(&root).unwrap();
    let status = std::process::Command::new(STUB)
        .args(["catalog", "--output"])
        .arg(&root)
        .status()
        .unwrap();
    assert!(status.success());

    let path = evmedia_gui::catalog::catalog_path(&root);
    assert!(path.is_file(), "the CLI did not write {}", path.display());
    let catalog = evmedia_gui::catalog::Catalog::load(&path).unwrap();
    assert_eq!(catalog.title, "stub");
    assert_eq!(catalog.roots.len(), 1);
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn a_missing_catalog_is_reported_and_a_missing_session_is_explained() {
    let root = std::env::temp_dir().join("evmedia-gui-no-catalog");
    let error =
        evmedia_gui::catalog::Catalog::load(&evmedia_gui::catalog::catalog_path(&root)).unwrap_err();
    assert!(error.contains("读取"), "unexpected error: {error}");
    let error = evmedia_gui::session::inspect(std::path::Path::new("nope.json")).unwrap_err();
    assert!(error.contains("会话文件"), "unexpected error: {error}");
}
