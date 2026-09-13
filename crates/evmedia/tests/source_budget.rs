//! Structural promises, checked by the test suite rather than by review.
//!
//! `evmedia` is the crate that depends on everything, so it is the natural place to assert
//! properties of the workspace as a whole.

use std::path::{Path, PathBuf};

/// The hard cap the module layout is designed around.
const MAX_LINES: usize = 500;

/// A file may approach the cap, but the margin should stay real rather than nominal.
const COMFORTABLE: usize = 420;

fn workspace_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

fn rust_files(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            rust_files(&path, out);
        } else if path.extension().is_some_and(|ext| ext == "rs") {
            out.push(path);
        }
    }
}

fn sources_of(relative: &str) -> Vec<(PathBuf, String)> {
    let mut files = Vec::new();
    rust_files(&workspace_root().join(relative), &mut files);
    files
        .into_iter()
        .map(|path| {
            let text = std::fs::read_to_string(&path).unwrap_or_default();
            (path, text)
        })
        .collect()
}

#[test]
fn no_source_file_exceeds_the_line_budget() {
    let files = sources_of("crates");
    assert!(!files.is_empty(), "no sources found; is the workspace layout still as expected?");
    let offenders: Vec<String> = files
        .iter()
        .map(|(path, text)| (path, text.lines().count()))
        .filter(|(_, lines)| *lines > MAX_LINES)
        .map(|(path, lines)| format!("{}: {lines} lines", path.display()))
        .collect();
    assert!(
        offenders.is_empty(),
        "these files are over the {MAX_LINES}-line budget:\n{}",
        offenders.join("\n")
    );
}

/// The report on this project's history said one file had grown to 862 lines. A regression
/// back toward that is worth failing on before it reaches the hard cap.
#[test]
fn no_source_file_is_close_to_the_budget() {
    let files = sources_of("crates");
    let offenders: Vec<String> = files
        .iter()
        .map(|(path, text)| (path, text.lines().count()))
        .filter(|(_, lines)| *lines > COMFORTABLE)
        .map(|(path, lines)| format!("{}: {lines} lines", path.display()))
        .collect();
    assert!(
        offenders.is_empty(),
        "these files are within {COMFORTABLE}..={MAX_LINES} lines; split them before they grow:\n{}",
        offenders.join("\n")
    );
}

/// Portability is a structural property of `evmedia-core`, not a habit. If a Windows call
/// appears here, the crate can no longer be built or tested off Windows.
#[test]
fn the_portable_core_does_not_touch_windows() {
    let offenders: Vec<String> = sources_of("crates/evmedia-core/src")
        .into_iter()
        .filter(|(_, text)| text.contains("windows_sys") || text.contains("windows-sys"))
        .map(|(path, _)| path.display().to_string())
        .collect();
    assert!(
        offenders.is_empty(),
        "evmedia-core must make no Windows API call; found it in:\n{}",
        offenders.join("\n")
    );
}

/// The GUI drives the CLI and must not be able to do anything else. Keeping the core crates out
/// of its manifest is what makes that enforceable at link time instead of by convention.
#[test]
fn the_gui_cannot_link_the_core() {
    let manifest = std::fs::read_to_string(workspace_root().join("crates/evmedia-gui/Cargo.toml"))
        .expect("read the GUI manifest");
    // Strip comments: the manifest explains this rule in prose, and that text must not trip it.
    let code: String = manifest
        .lines()
        .map(|line| line.split('#').next().unwrap_or(""))
        .collect::<Vec<_>>()
        .join("\n");
    assert!(
        !code.contains("evmedia-core"),
        "evmedia-gui must reach the product only through the CLI, not by linking evmedia-core"
    );
    assert!(
        !code.contains("evmedia-win"),
        "evmedia-gui must reach the product only through the CLI, not by linking evmedia-win"
    );
    assert!(
        code.contains("evmedia-contract"),
        "evmedia-gui should still share the argv and event definitions with the CLI"
    );
}
