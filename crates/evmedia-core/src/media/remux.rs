//! Remux the merged transport stream into MP4 without re-encoding.
//!
//! A missing ffmpeg is a normal outcome, not an error: the merged `.ts` is already usable, and
//! the caller should report that rather than fail the run.

use anyhow::Result;
use evmedia_contract::{ArtifactKind, Event, Reporter};
use std::path::{Path, PathBuf};

pub struct RemuxOutcome {
    pub path: PathBuf,
    pub ok: bool,
}

pub fn remux_mp4(ts: &Path, mp4: &Path, reporter: &Reporter) -> Result<RemuxOutcome> {
    // The audio map is optional (`0:a:0?`) because some lessons carry no audio track.
    let status = std::process::Command::new("ffmpeg")
        .args([
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            &ts.to_string_lossy(),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            &mp4.to_string_lossy(),
        ])
        .status();

    let ok = match status {
        Ok(status) if status.success() => {
            reporter.info(format!("mp4 -> {}", mp4.display()));
            let bytes = std::fs::metadata(mp4).map(|meta| meta.len()).unwrap_or(0);
            reporter.event(&Event::Artifact {
                kind: ArtifactKind::Mp4,
                path: mp4.display().to_string(),
                bytes,
            });
            true
        }
        Ok(status) => {
            reporter.info(format!("ffmpeg exited with {status}; the merged .ts is still usable"));
            false
        }
        Err(error) => {
            reporter.info(format!("could not run ffmpeg ({error}); the merged .ts is still usable"));
            false
        }
    };
    Ok(RemuxOutcome { path: mp4.to_path_buf(), ok })
}
