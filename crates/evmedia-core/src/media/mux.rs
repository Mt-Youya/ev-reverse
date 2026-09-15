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
    let expected = indexes.iter().copied().max().unwrap_or(0);
    let complete = indexes.len() as u32 == expected + 1;
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
