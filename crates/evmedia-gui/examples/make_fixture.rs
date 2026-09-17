//! Writes a catalog fixture in the exact shape `evmedia catalog` produces, so the batch path can be
//! exercised without an account, a session or a network.
//!
//!   cargo run -p evmedia-gui --example make_fixture -- <root> [videos] [pre-exported]
//!
//! `pre-exported` writes that many dummy output files up front, which is how the "already there,
//! skipped" state gets onto a screen.
//!
//! It is deliberately not part of the product: the window reads its catalog from the CLI, and nothing
//! in `src/` knows this example exists.

use std::path::PathBuf;

fn escape(value: &str) -> String {
    value.replace('\\', "\\\\").replace('"', "\\\"")
}

fn main() {
    let mut args = std::env::args().skip(1);
    let root =
        PathBuf::from(args.next().expect("usage: make_fixture <root> [videos] [pre-exported]"));
    let wanted: usize = args.next().and_then(|value| value.parse().ok()).unwrap_or(12);
    let pre_exported: usize = args.next().and_then(|value| value.parse().ok()).unwrap_or(0);

    // The shape the real catalog has: each root holds a folder whose name repeats the root's, and
    // each course holds a folder repeating the course's own name. That nesting is what the path
    // builder has to fold away, so a fixture without it would test nothing.
    let courses: [(&str, i64, &[&str]); 3] = [
        ("Course A (React)", 315187, &["01. Getting started", "02. Hooks"]),
        ("Course B (uni-app)", 315188, &["01. Basics"]),
        ("Course C (algorithms)", 315189, &["01. Arrays", "02. Sorting"]),
    ];

    // Built as vectors and joined: hand-concatenated JSON with trailing commas is invalid, and "the
    // fixture is not valid JSON" is a confusing way to find that out.
    let mut roots: Vec<String> = Vec::new();
    let mut written = 0usize;
    let mut file_id = 903_780i64;

    for (name, course, chapters) in courses {
        let mut chapter_nodes: Vec<String> = Vec::new();
        for chapter in chapters {
            let mut videos: Vec<String> = Vec::new();
            // Every chapter numbers from 01, which is what makes a flat per-course directory
            // collide.
            for index in 1..=3 {
                if written >= wanted {
                    break;
                }
                file_id += 1;
                written += 1;
                let title = format!("0{index}. {chapter} lesson.mp4");
                let id = format!("{course}:{file_id}");
                videos.push(format!(
                    r#"{{"id":"{id}","title":"{}","kind":"video","children":[],"video":{{"id":"{id}","duration_seconds":{},"source":"evs-{file_id}"}}}}"#,
                    escape(&title),
                    600 + (file_id % 1800)
                ));
            }
            if videos.is_empty() {
                continue;
            }
            let chapter_id = format!("chapter-{course}-{}", escape(chapter));
            chapter_nodes.push(format!(
                r#"{{"id":"{chapter_id}","title":"{}","kind":"folder","children":[{}]}}"#,
                escape(chapter),
                videos.join(",")
            ));
        }
        if chapter_nodes.is_empty() {
            continue;
        }
        // The course folder carries the course's name, and inside it a folder of the same name holds
        // the chapters — the repetition the real catalog has.
        let inner = format!(
            r#"{{"id":"course-{course}-inner","title":"{}","kind":"folder","children":[{}]}}"#,
            escape(name),
            chapter_nodes.join(",")
        );
        roots.push(format!(
            r#"{{"id":"course-{course}","title":"{}","kind":"folder","children":[{inner}]}}"#,
            escape(name)
        ));
    }

    let catalog = format!(r#"{{"title":"account 119354","roots":[{}]}}"#, roots.join(","));
    // The CLI writes `catalog.json` and `index.json` at the output root, and the window reads them
    // from there. Writing them anywhere else made this fixture stop matching the product.
    std::fs::create_dir_all(&root).expect("create the export root");
    std::fs::write(root.join("catalog.json"), catalog).expect("write catalog.json");

    // The session file is only here so the window's "the file exists" check passes; it is not a
    // credential and the stub CLI never reads it.
    std::fs::write(
        root.join("session.json"),
        r#"{"token":"Bearer stub-not-a-real-token","data_key":"0123456789abcdef","sign_secret":"stub","machine_id":"stub","busi_id":1}"#,
    )
    .expect("write session.json");

    println!("{written} videos written to {}", root.join("catalog.json").display());

    // Pre-existing outputs, so a verification run can see the "skipped" path as well as "done". The
    // path mirrors what the window plans, including the folded-away repeated level.
    for index in 1..=pre_exported {
        let (name, _, chapters) = courses[courses.len() - 1];
        let chapter = chapters[0];
        let title = format!("0{index}. {chapter} lesson.mp4");
        let path = root
            .join("out")
            .join(name)
            .join(chapter)
            .join(&title);
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let _ = std::fs::write(&path, b"already exported");
    }
    if pre_exported > 0 {
        println!("{pre_exported} outputs already exist and should show as skipped");
    }
}
