//! A stand-in for `evmedia`, so the batch machinery can be tested without a session, a network or a
//! video.
//!
//! It is a *test double for the wire*, not a second implementation of the product: it accepts the
//! argv `export-evs` accepts, writes the same newline-delimited JSON events to stdout
//! (`docs/CLI-CONTRACT.md`), and creates the output file. Nothing about segments, keys or decryption
//! exists here, so it cannot quietly become the thing under test.
//!
//! Controlled by two environment variables, both optional:
//!   `EVSTUB_SEGMENTS` — how many segments to simulate (default 4)
//!   `EVSTUB_TAIL_MS`  — how long to keep working after the last segment (default 0)

use std::{
    io::Write,
    path::PathBuf,
    time::{Duration, Instant},
};

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    if argv.iter().any(|token| token == "--version") {
        println!("evmedia 0.1.0");
        return;
    }
    if argv.first().map(String::as_str) == Some("catalog") {
        run_catalog(&argv);
        return;
    }
    run_export(&argv);
}

/// The minimum of the argv this stub understands. Anything missing is a hard error, because a stub
/// that runs with the wrong arguments would let a real bug pass.
fn flag(argv: &[String], name: &str) -> Option<String> {
    let index = argv.iter().position(|token| token == name)?;
    argv.get(index + 1).cloned()
}

fn emit(value: serde_json::Value) {
    // Flushed per line: the window reads this pipe while the process is alive, and a cancelled run
    // must still deliver its `finished` event.
    println!("{value}");
    let _ = std::io::stdout().flush();
}

fn note(message: &str) {
    // Human output goes to stderr in JSON mode, exactly as the CLI does.
    eprintln!("{message}");
}

fn run_catalog(argv: &[String]) {
    let output = flag(argv, "--output").unwrap_or_else(|| ".".to_string());
    let root = PathBuf::from(&output);
    let _ = std::fs::create_dir_all(&root);
    // The same layout `evmedia catalog` writes: `catalog.json` and `index.json` at the output root.
    let tree = serde_json::json!({
        "title": "stub",
        "roots": [{ "id": "f", "title": "one folder", "kind": "folder", "children": [] }]
    });
    let _ = std::fs::write(
        root.join("catalog.json"),
        serde_json::to_vec_pretty(&tree).unwrap(),
    );
    let _ = std::fs::write(
        root.join("index.json"),
        serde_json::to_vec_pretty(&serde_json::json!({ "courses": { "1": {} } })).unwrap(),
    );
    note("catalog written");
}

fn run_export(argv: &[String]) {
    let segments: usize = std::env::var("EVSTUB_SEGMENTS")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(4);
    let tail = std::env::var("EVSTUB_TAIL_MS")
        .ok()
        .and_then(|value| value.parse().ok())
        .map(Duration::from_millis)
        .unwrap_or_default();

    emit(serde_json::json!({"event":"started","protocol":1,
        "command":std::env::args().skip(1).collect::<Vec<_>>(),"app_version":"0.1.0"}));

    let output = flag(argv, "--output").unwrap_or_else(|| "out.mp4".to_string());
    let work = flag(argv, "--work")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("work"));
    let phase = flag(argv, "--phase").unwrap_or_else(|| "all".to_string());
    let stop_file = flag(argv, "--stop-file").map(PathBuf::from);
    let force = argv.iter().any(|token| token == "--force");
    let output = PathBuf::from(output);

    if output.exists() && !force {
        note("output already exists");
        emit(
            serde_json::json!({"event":"finished","status":"failed","exit_code":1,
            "message":"output already exists"}),
        );
        std::process::exit(1);
    }

    if phase != "convert" && phase != "merge" && phase != "publish" {
        let _ = std::fs::create_dir_all(&work);
        let _ = std::fs::write(work.join("stub-download.started"), b"");
        emit(serde_json::json!({"event":"stage","name":"download","state":"begin","detail":""}));
        note(&format!("would download {segments} segment(s)"));

        let started = Instant::now();
        let mut done = 0usize;
        for index in 0..segments {
            if let Some(path) = &stop_file {
                if path.exists() {
                    emit(
                        serde_json::json!({"event":"stage","name":"download","state":"end",
                    "detail":format!("{done}/{segments} segment(s) of {segments}")}),
                    );
                    emit(
                        serde_json::json!({"event":"finished","status":"cancelled","exit_code":0,
                    "message":"stopped on request"}),
                    );
                    return;
                }
            }
            std::thread::sleep(Duration::from_millis(20));
            done += 1;
            emit(
                serde_json::json!({"event":"segment","index":index,"file":format!("stub-{index}.ts"),
            "state":"done","attempt":1}),
            );
            emit(
                serde_json::json!({"event":"progress","stage":"download","keys":done,"urls":done,
            "segments":segments,"done":done,"failed":0,"elapsed_secs":started.elapsed().as_secs()}),
            );
            note(&format!("downloaded {done}/{segments}"));
        }

        if !tail.is_zero() {
            std::thread::sleep(tail);
        }
        if let Some(path) = &stop_file {
            if path.exists() {
                emit(
                    serde_json::json!({"event":"finished","status":"cancelled","exit_code":0,
                "message":"stopped during the merge"}),
                );
                return;
            }
        }

        let _ = std::fs::create_dir_all(&work);
        let _ = std::fs::write(work.join("stub-download.ready"), b"");
        emit(
            serde_json::json!({"event":"stage","name":"download","state":"end",
            "detail":format!("{segments}/{segments} segment(s) of {segments}")}),
        );
        if phase == "download" {
            emit(
                serde_json::json!({"event":"finished","status":"complete","exit_code":0,
                "message":format!("downloaded {segments} segment(s)")}),
            );
            return;
        }
    }

    if phase != "publish" {
        let _ = std::fs::create_dir_all(&work);
        let _ = std::fs::write(work.join("stub-convert.started"), b"");
        emit(serde_json::json!({"event":"stage","name":"merge","state":"begin","detail":""}));
        if !tail.is_zero() {
            std::thread::sleep(tail);
        }
        let _ = std::fs::write(work.join("stub-convert.finished"), b"");
        if phase == "merge" {
            emit(
                serde_json::json!({"event":"finished","status":"complete","exit_code":0,
                "message":format!("merged {segments} segment(s)")}),
            );
            return;
        }
    }

    emit(serde_json::json!({"event":"stage","name":"remux","state":"begin","detail":""}));
    let _ = std::fs::write(work.join("stub-publish.started"), b"");
    if phase == "publish" && !tail.is_zero() {
        std::thread::sleep(tail);
    }

    if let Some(parent) = output.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let bytes = vec![0u8; 1024];
    if std::fs::write(&output, &bytes).is_err() {
        emit(
            serde_json::json!({"event":"finished","status":"failed","exit_code":1,
            "message":"cannot write the output"}),
        );
        std::process::exit(1);
    }
    emit(
        serde_json::json!({"event":"artifact","kind":"mp4","path":output.display().to_string(),
        "bytes":bytes.len()}),
    );
    let _ = std::fs::write(work.join("stub-publish.finished"), b"");
    emit(
        serde_json::json!({"event":"finished","status":"complete","exit_code":0,
        "message":format!("merged {segments} segment(s)")}),
    );
}
