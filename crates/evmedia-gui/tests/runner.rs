//! The two things that made a whole batch look broken: a success that reported nothing, and a stop
//! that could not be hurried.
//!
//! Both are about what the *window* concludes from the CLI, so they are tested against the stub that
//! speaks the CLI's protocol rather than against the CLI's internals.

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

const STUB: &str = env!("CARGO_BIN_EXE_evmedia-stub");

#[derive(Default)]
struct Collect {
    logs: Mutex<Vec<String>>,
    events: Mutex<Vec<Event>>,
}

impl Sink for Collect {
    fn entry(&self, _entry: &JobEntry) {}
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
        let root = std::env::temp_dir()
            .join(format!("evmedia-gui-runner-{name}-{}", std::process::id()));
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

    fn item(&self, title: &str) -> JobItem {
        JobItem {
            id: "315187:903780".to_string(),
            course: 315187,
            file: 903780,
            title: title.to_string(),
            duration_seconds: Some(60.0),
            path: vec!["chapter".into()],
            output: evmedia_gui::plan::output_of(&self.root, &["chapter".to_string()], title, "mp4"),
            work: evmedia_gui::plan::work_of(&self.root, 315187, 903780),
        }
    }

    fn row(item: JobItem) -> JobEntry {
        JobEntry::new(item, vec!["export-evs".into()], JobStatus::Queued, String::new())
    }

    fn snapshot(&self) -> Snapshot {
        self.queue.snapshot()
    }
}

/// A run that reported `complete` must be `Complete`, and the transcript of that run must be on disk
/// in full — the in-memory tail is capped at 400 lines, and the line that explains a failure is
/// exactly the kind that a cap drops.
#[test]
fn a_finished_run_is_complete_and_keeps_its_whole_transcript() {
    let fixture = Fixture::new("transcript");
    let item = fixture.item("lesson one");
    let work = item.work.clone();
    fixture.queue.start(
        vec![Fixture::row(item)],
        fixture.options.clone(),
        1,
        STUB.to_string(),
    );
    assert!(fixture.queue.wait_idle(Duration::from_secs(60)), "the run never finished");

    let snapshot = fixture.snapshot();
    assert_eq!(snapshot.items[0].status, JobStatus::Complete);

    // The transcript exists, and it holds the CLI's own lines rather than only the window's summary.
    let log_path = snapshot.items[0]
        .log_path
        .as_ref()
        .expect("the row must publish where its transcript is");
    assert_eq!(PathBuf::from(log_path), work.join(evmedia_gui::job::STDERR_LOG));
    let transcript = std::fs::read_to_string(log_path).expect("the transcript must exist");
    assert!(transcript.contains("downloaded 4/4"), "unexpected transcript: {transcript}");
    assert!(
        transcript.contains("would download 4 segment"),
        "the CLI's opening line must be in the transcript: {transcript}"
    );

    // And the protocol really ended with a verdict, not with a stage line.
    let events = fixture.sink.events.lock().unwrap().clone();
    assert!(
        matches!(events.last(), Some(Event::Finished { .. })),
        "the last event must be `finished`, got {:?}",
        events.last()
    );
    let _ = std::fs::remove_dir_all(&fixture.root);
}

/// The stop button asks the CLI to stop; the force button ends it now. Both have to leave the rows
/// readable, and neither may leave a worker behind.
#[test]
fn a_forced_stop_settles_the_row_and_leaves_nothing_running() {
    let fixture = Fixture::new("force");
    let mut options = fixture.options.clone();
    // The lesson dawdles after its last segment, so the kill lands while it is genuinely working.
    options.stub_tail_ms = 5000;
    fixture
        .queue
        .start(vec![Fixture::row(fixture.item("a long lesson"))], options, 1, STUB.to_string());

    // Wait until the worker is up, then stop it the way a user does when "停止" seems ignored.
    let deadline = std::time::Instant::now() + Duration::from_secs(30);
    let mut stopped = 0;
    while std::time::Instant::now() < deadline && stopped == 0 {
        std::thread::sleep(Duration::from_millis(300));
        stopped = fixture.queue.kill_running();
    }
    assert!(stopped > 0, "there was no running worker to force");
    assert!(fixture.queue.snapshot().stopping, "the window must be able to say it is stopping");
    assert!(fixture.queue.wait_idle(Duration::from_secs(30)), "the runner never settled");

    let snapshot = fixture.snapshot();
    assert!(!snapshot.running);
    assert!(!snapshot.stopping);
    // Nothing was reported complete, so nothing may claim to be: a killed run is a failure.
    assert_eq!(snapshot.items[0].status, JobStatus::Failed);
    assert!(
        snapshot.items[0].message.contains("强制结束") || snapshot.items[0].message.contains("没有报告"),
        "unexpected message: {}",
        snapshot.items[0].message
    );
    // And the row is retryable, which is the point of keeping it.
    assert_eq!(fixture.queue.retry_finished(), 1);
    assert_eq!(fixture.queue.snapshot().items[0].status, JobStatus::Queued);
    let _ = std::fs::remove_dir_all(&fixture.root);
}

/// A second run of the same lesson truncates its transcript rather than appending to it: a log that
/// holds two attempts is a log nobody can read.
#[test]
fn a_rerun_replaces_its_transcript() {
    let fixture = Fixture::new("rerun");
    let item = fixture.item("lesson one");
    let log_path = item.work.join(evmedia_gui::job::STDERR_LOG);
    fixture.queue.start(
        vec![Fixture::row(item.clone())],
        fixture.options.clone(),
        1,
        STUB.to_string(),
    );
    assert!(fixture.queue.wait_idle(Duration::from_secs(60)));
    let first = std::fs::read_to_string(&log_path).unwrap();
    assert!(first.contains("downloaded 4/4"));

    // Forget the output so the second run actually runs, then look at the file again.
    let _ = std::fs::remove_file(&item.output);
    fixture.queue.retry_finished();
    fixture.queue.start_with_existing(fixture.options.clone(), 1, STUB.to_string());
    assert!(fixture.queue.wait_idle(Duration::from_secs(60)));
    let second = std::fs::read_to_string(&log_path).unwrap();
    assert_eq!(
        second.matches("would download 4 segment").count(),
        1,
        "the second run appended to the first one's transcript"
    );
    let _ = std::fs::remove_dir_all(&fixture.root);
}

#[test]
fn a_downloaded_lesson_starts_conversion_without_waiting_for_the_next_one() {
    let fixture = Fixture::new("two-phase");
    let first = fixture.item("first");
    let mut second = fixture.item("second");
    second.id = "315187:903781".to_string();
    second.file = 903781;
    second.work = evmedia_gui::plan::work_of(&fixture.root, second.course, second.file);
    fixture.queue.start(
        vec![Fixture::row(first.clone()), Fixture::row(second.clone())],
        fixture.options.clone(),
        1,
        STUB.to_string(),
    );
    assert!(fixture.queue.wait_idle(Duration::from_secs(60)), "the batch never finished");
    assert_eq!(fixture.snapshot().items.iter().filter(|item| item.status == JobStatus::Complete).count(), 2);
    let second_download = std::fs::metadata(second.work.join("stub-download.ready")).unwrap().modified().unwrap();
    let first_convert = std::fs::metadata(first.work.join("stub-convert.started")).unwrap().modified().unwrap();
    assert!(first_convert <= second_download, "conversion waited for an unrelated lesson download");
    let _ = std::fs::remove_dir_all(&fixture.root);
}
