//! Concatenate decrypted segments in index order.
//!
//! The name of the result carries the verdict: `lesson.ts` only when the indexes form an
//! unbroken `0..=max` run, otherwise `lesson.partial.ts`. A file called `lesson.ts` is a
//! promise, and callers rely on it.

use anyhow::Result;
use evmedia_contract::{ArtifactKind, Event, Reporter};
use std::path::{Path, PathBuf};

pub struct MergeOutcome {
    pub path: PathBuf,
    pub complete: bool,
    pub bytes: u64,
    pub count: usize,
}

pub fn merge_lesson(
    dec_dir: &Path,
    output: &Path,
    indexes: &[u32],
    reporter: &Reporter,
) -> Result<MergeOutcome> {
    merge_lesson_known(dec_dir, output, indexes, indexes.iter().copied().max(), reporter)
}

/// Include the highest index observed even when its key has not arrived yet.
pub fn merge_lesson_known(
    dec_dir: &Path,
    output: &Path,
    indexes: &[u32],
    last_seen: Option<u32>,
    reporter: &Reporter,
) -> Result<MergeOutcome> {
    let expected = last_seen.into_iter().chain(indexes.iter().copied()).max();
    let complete = expected.is_some_and(|last| indexes.len() as u64 == u64::from(last) + 1)
        && indexes.iter().enumerate().all(|(position, index)| position as u64 == u64::from(*index));
    let merged = output.join(if complete { "lesson.ts" } else { "lesson.partial.ts" });

    let mut destination = std::fs::File::create(&merged)?;
    for index in indexes {
        let path = dec_dir.join(format!("{index:06}.ts"));
        std::io::copy(&mut std::fs::File::open(&path)?, &mut destination)?;
    }
    destination.sync_all()?;

    let bytes = std::fs::metadata(&merged)?.len();
    reporter.info(format!(
        "merged {} segment(s) -> {}{}",
        indexes.len(),
        merged.display(),
        if complete { "" } else { "  [INCOMPLETE]" }
    ));
    reporter.event(&Event::Artifact {
        kind: ArtifactKind::Ts,
        path: merged.display().to_string(),
        bytes,
    });
    Ok(MergeOutcome { path: merged, complete, bytes, count: indexes.len() })
}
