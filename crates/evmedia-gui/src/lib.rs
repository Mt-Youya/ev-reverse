//! evmedia desktop shell.
//!
//! The CLI is the product; this window is a way to drive it. Every row in the export queue ends up
//! as an argv for `evmedia.exe`, run as a child process, and every number on screen came out of
//! that process's own JSON event stream. Nothing about catalogues, segments, keys or decryption is
//! implemented on this side — `evmedia-core` is deliberately not a dependency of this crate, and
//! `crates/evmedia/tests/source_budget.rs` fails if that changes.
//!
//! Concurrency is the one thing the window adds: N workers, each one an independent
//! `evmedia export-evs`. Inside a worker, `--jobs` parallelises that lesson's segments.

pub mod catalog;
pub mod config;
pub mod job;
pub mod locate;
pub mod log;
pub mod plan;
pub mod protocol;
pub mod queue;
pub mod refresh;
pub mod runner;
pub mod server;
pub mod session;
pub mod sniff;
/// A job object around each run, so a forced stop reaches the ffmpeg the CLI spawned instead of
/// orphaning it. Windows only: nothing else has process trees to contain.
#[cfg(windows)]
pub mod winjob;

pub use server::AppState;

/// Drop a UTF-8 byte-order mark.
///
/// Every JSON file this app reads can have been written by something else — Notepad and PowerShell
/// both add a BOM by default on Windows, and `serde_json` refuses to parse past one. The failure it
/// produces ("expected value at line 1 column 1") points nowhere near the cause, so the mark is
/// removed once here instead of being diagnosed later.
pub fn strip_bom(bytes: &[u8]) -> &[u8] {
    bytes.strip_prefix(&[0xEF, 0xBB, 0xBF]).unwrap_or(bytes)
}

#[cfg(test)]
mod tests {
    use super::strip_bom;

    #[test]
    fn a_byte_order_mark_is_not_a_syntax_error() {
        assert_eq!(strip_bom(b"\xEF\xBB\xBF{}"), b"{}");
        assert_eq!(strip_bom(b"{}"), b"{}");
        assert_eq!(strip_bom(b""), b"");
    }
}
