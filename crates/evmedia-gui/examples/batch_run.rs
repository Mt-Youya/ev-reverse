//! Runs a whole batch through the queue with no window, against the stub CLI.
//!
//! The integration test proves the queue's behaviour; this proves the *shape* of a real run — a
//! catalog written by `evmedia catalog`, a selection, per-video work directories, and one output file
//! per lesson. It is the closest thing to pressing "开始导出" that can run without a screen, and it is
//! how the batch path is checked after a change.
//!
//!   cargo run -p evmedia-gui --example batch_run -- <root> <stub exe> [workers]

use evmedia_gui::{
    catalog::Catalog,
    job::{JobEntry, JobStatus},
    plan::{self, Options},
    protocol::Sink,
    queue::Queue,
};
use std::{path::PathBuf, sync::Arc, time::Duration};

/// Prints every movement, so a failure is readable rather than just "it did not work".
struct Echo;

impl Sink for Echo {
    fn entry(&self, entry: &JobEntry) {
        let total = entry.progress.total;
        let done = entry.progress.done;
        println!(
            "  [{:?}] {} - {}{}",
            entry.status,
            entry.item.title,
            entry.message,
            if total > 0 { format!(" ({done}/{total} segments)") } else { String::new() }
        );
    }

    fn log(&self, _id: &str, line: &str) {
        println!("    | {line}");
    }
}

fn main() {
    let mut args = std::env::args().skip(1);
    let root = PathBuf::from(args.next().expect("usage: batch_run <root> <stub exe> [workers]"));
    let cli = args.next().expect("usage: batch_run <root> <stub exe> [workers]");
    let workers: usize = args.next().and_then(|value| value.parse().ok()).unwrap_or(3);
    // A run's child is started in the lesson's `--work` directory, so the root has to be absolute
    // here exactly as `GuiConfig::options` makes it for the window.
    let root = std::fs::canonicalize(&root).expect("the fixture root must exist");

    let catalog = Catalog::load(&evmedia_gui::catalog::catalog_path(&root))
        .expect("the catalog fixture must exist");
    let selected: Vec<String> = catalog.roots.iter().flat_map(|node| node.video_ids()).collect();
    println!("catalog: {}, {} selected, {workers} at a time", catalog.title, selected.len());

    let options = Options {
        session: root.join("session.json"),
        account: 119354,
        root: root.clone(),
        jobs: 4,
        extension: "mp4".into(),
        ffmpeg: "ffmpeg".into(),
        ffprobe: "ffprobe".into(),
        force: false,
        stub_tail_ms: 0,
    };

    let rows: Vec<JobEntry> = catalog
        .selected(&selected)
        .into_iter()
        .map(|video| {
            let item = plan::item_of(&options, &video);
            let argv = plan::argv(&item, &options);
            JobEntry::new(item, argv, JobStatus::Queued, String::new())
        })
        .collect();
    println!("planned {} export task(s)", rows.len());
    if let Some(row) = rows.first() {
        println!("  first: {}", row.item.output.display());
    }

    let queue = Queue::new(Arc::new(Echo));
    queue.start(rows, options, workers, cli);
    if !queue.wait_idle(Duration::from_secs(300)) {
        eprintln!("the batch did not finish within 5 minutes");
        std::process::exit(1);
    }

    let snapshot = queue.snapshot();
    let mut counts = std::collections::BTreeMap::new();
    for entry in &snapshot.items {
        *counts.entry(format!("{:?}", entry.status)).or_insert(0usize) += 1;
    }
    println!("\nresult: {counts:?}");

    let missing: Vec<String> = snapshot
        .items
        .iter()
        .filter(|entry| entry.status == JobStatus::Complete)
        .filter(|entry| !entry.item.output.is_file())
        .map(|entry| entry.item.output.display().to_string())
        .collect();
    if !missing.is_empty() {
        eprintln!("these are reported complete but their file is missing:\n{}", missing.join("\n"));
        std::process::exit(1);
    }
    let unfinished: Vec<String> = snapshot
        .items
        .iter()
        .filter(|entry| entry.status != JobStatus::Complete)
        .map(|entry| format!("{:?} {}", entry.status, entry.item.title))
        .collect();
    if !unfinished.is_empty() {
        eprintln!("these did not complete:\n{}", unfinished.join("\n"));
        std::process::exit(1);
    }
    println!("all complete; outputs are under {}", root.join("out").display());
}
