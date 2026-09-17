//! A rerun has to be able to finish.
//!
//! `export-evs` merges a lesson into `work/<course>-<file>/lesson.ts` on every run, and `decode-ev`
//! refuses to overwrite its output — correctly, because for that command the file *is* the
//! deliverable. The two together meant a second export of the same lesson died with
//! `output already exists: …/lesson.ts` before it reached the remux, so "delete the video and export
//! it again" — the most natural thing to try — could never work.
//!
//! This pins the rule the export path relies on: an intermediate in a lesson's own work directory is
//! cleared, a deliverable is not.

use std::path::Path;

/// The same rule `remove_if_present` implements in the CLI, asserted where it is cheap to assert.
fn clear(path: &Path) -> std::io::Result<()> {
    match std::fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

#[test]
fn clearing_an_intermediate_is_idempotent_and_a_real_failure_is_not_swallowed() {
    let work = std::env::temp_dir().join(format!("evmedia-rerun-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&work);
    std::fs::create_dir_all(&work).unwrap();
    let merged = work.join("lesson.ts");

    // A first run has nothing to clear, and that is not an error.
    assert!(clear(&merged).is_ok());

    std::fs::write(&merged, b"the previous merge").unwrap();
    assert!(clear(&merged).is_ok(), "a leftover intermediate must not fail the rerun");
    assert!(!merged.exists());

    // Clearing again is still not an error, so a retry after a retry is safe.
    assert!(clear(&merged).is_ok());

    // A directory where the file should be is a real problem and has to be reported: silently
    // continuing would produce a merge that cannot be written and an error far from the cause.
    let stubborn = work.join("a-directory");
    std::fs::create_dir_all(&stubborn).unwrap();
    let error = clear(&stubborn).expect_err("a directory is not an intermediate");
    assert_ne!(error.kind(), std::io::ErrorKind::NotFound);

    let _ = std::fs::remove_dir_all(&work);
}
