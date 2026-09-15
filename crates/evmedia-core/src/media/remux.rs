//! Produce a compatible MP4 and decode-check it before publishing the artifact.

use anyhow::{bail, Context, Result};
use evmedia_contract::{ArtifactKind, Event, Reporter};
use std::{path::{Path, PathBuf}, process::Command};

pub struct RemuxOutcome {
    pub path: PathBuf,
    pub ok: bool,
}

fn checked(command: &mut Command, stage: &str) -> Result<Vec<u8>> {
    let output = command.output().with_context(|| format!("{stage}: could not start media tool"))?;
    // All commands run at error log level. Some decoders print errors but still exit zero.
    if !output.status.success() || !output.stderr.is_empty() {
        bail!("{stage}: {}: {}", output.status, String::from_utf8_lossy(&output.stderr).trim());
    }
    Ok(output.stdout)
}

pub fn remux_mp4(ts: &Path, mp4: &Path, reporter: &Reporter) -> Result<RemuxOutcome> {
    let probe = checked(Command::new("ffprobe")
        .args(["-v", "error", "-show_entries", "stream=codec_type,codec_name,pix_fmt", "-of", "json"])
        .arg(ts), "inspect source")?;
    let metadata: serde_json::Value = serde_json::from_slice(&probe).context("parse ffprobe output")?;
    let streams = metadata["streams"].as_array().context("ffprobe returned no streams")?;
    let video = streams.iter().find(|stream| stream["codec_type"] == "video")
        .context("source has no video stream")?;
    let audio = streams.iter().find(|stream| stream["codec_type"] == "audio");
    let copy_video = video["codec_name"] == "h264" && video["pix_fmt"] == "yuv420p";
    let copy_audio = audio.map_or(true, |stream| stream["codec_name"] == "aac");

    let temporary = mp4.with_extension(format!("{}.pending.mp4", std::process::id()));
    std::fs::OpenOptions::new().write(true).create_new(true).open(&temporary)
        .with_context(|| format!("reserve {}", temporary.display()))?;
    let result = (|| -> Result<u64> {
        reporter.info(if copy_video && copy_audio {
            "remuxing H.264/AAC without re-encoding"
        } else {
            "converting unsupported streams to H.264 yuv420p / AAC"
        });
        let mut command = Command::new("ffmpeg");
        command.args(["-v", "error", "-nostdin", "-y", "-xerror", "-err_detect", "explode", "-i"])
            .arg(ts).args(["-map", "0:v:0", "-map", "0:a:0?"]);
        if copy_video {
            command.args(["-c:v", "copy"]);
        } else {
            command.args(["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"]);
        }
        command.args(["-c:a", if copy_audio { "copy" } else { "aac" }]);
        checked(command.args(["-movflags", "+faststart"]).arg(&temporary), "write MP4")?;
        reporter.info("verifying the complete video and audio streams");
        checked(Command::new("ffmpeg")
            .args(["-v", "error", "-nostdin", "-xerror", "-err_detect", "explode", "-i"])
            .arg(&temporary)
            .args(["-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"]), "decode verification")?;
        let bytes = std::fs::metadata(&temporary)?.len();
        if bytes == 0 {
            bail!("ffmpeg produced an empty MP4");
        }
        std::fs::rename(&temporary, mp4).context("publish verified MP4")?;
        Ok(bytes)
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
    }
    let bytes = result?;
    reporter.info(format!("verified mp4 -> {}", mp4.display()));
    reporter.event(&Event::Artifact {
        kind: ArtifactKind::Mp4,
        path: mp4.display().to_string(),
        bytes,
    });
    Ok(RemuxOutcome { path: mp4.to_path_buf(), ok: true })
}
