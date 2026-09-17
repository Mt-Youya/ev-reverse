//! Publish MP4/MKV only after matching VOD duration and decoding all audio/video streams.
use crate::{full_export::Options, vod::Vod};
use anyhow::{bail, Context, Result};
use evmedia_contract::Reporter;
use serde_json::{json, Value};
use std::{fs, path::Path, process::{Command, Stdio}, time::Duration};

pub fn publish(ts: &Path, vod: &Vod, options: &Options, work: &Path, reporter: &Reporter) -> Result<()> {
    let output = &options.output;
    if let Some(parent) = output.parent().filter(|p| !p.as_os_str().is_empty()) {
        fs::create_dir_all(parent)?;
    }
    let extension = output.extension().context("missing container")?.to_string_lossy();
    let stem = output.file_stem().context("missing filename")?.to_string_lossy();
    let partial = output.with_file_name(format!("{stem}.partial.{extension}"));
    let log = work.join("media.log");
    reporter.info("合成完整视频和音频");
    let mut mux = Command::new(&options.ffmpeg);
    mux.args(["-v", "warning", "-nostdin", "-y", "-i"]).arg(ts)
        .args(["-map", "0:v:0", "-map", "0:a?", "-c", "copy"]);
    if extension == "mp4" { mux.args(["-movflags", "+faststart"]); }
    execute(mux.arg(&partial), &log)?;
    let probe = Command::new(&options.ffprobe).args(["-v", "error", "-show_streams", "-show_format", "-of", "json"])
        .arg(&partial).output().context("run ffprobe")?;
    if !probe.status.success() { bail!("ffprobe failed"); }
    let info: Value = serde_json::from_slice(&probe.stdout)?;
    let seconds: f64 = info["format"]["duration"].as_str().context("missing output duration")?.parse()?;
    validate_probe(&info, seconds, vod.seconds)?;
    if reporter.stopped() { bail!("export stopped"); }
    reporter.info("完整解码校验视频和音频");
    execute(Command::new(&options.ffmpeg).args(["-v", "error", "-nostdin", "-xerror", "-threads", "4", "-i"])
        .arg(&partial).args(["-map", "0:v", "-map", "0:a?", "-progress"])
        .arg(work.join("validation.progress")).args(["-nostats", "-f", "null", "-"]), &log)?;
    if reporter.stopped() { bail!("export stopped"); }
    let report = json!({"status":"complete", "segments":vod.names.len(),
        "playlist_seconds":vod.seconds, "output_seconds":seconds, "full_decode_verified":true,
        "output":output, "bytes":fs::metadata(&partial)?.len(), "streams":info["streams"]});
    for attempt in 0..=120 {
        match fs::rename(&partial, output) {
            Ok(()) => break,
            Err(e) if cfg!(windows) && e.raw_os_error() == Some(32) && attempt < 120 => {
                std::thread::sleep(Duration::from_millis(250));
            }
            Err(e) => return Err(e.into()),
        }
    }
    fs::write(work.join(format!("report-{extension}.json")), serde_json::to_vec_pretty(&report)?)?;
    reporter.info(format!("整课导出完成：{}（{seconds:.3} 秒，{} 段）", output.display(), vod.names.len()));
    Ok(())
}

pub fn validate_probe(info: &Value, seconds: f64, expected: f64) -> Result<()> {
    if !seconds.is_finite() || seconds <= 0.0 || (seconds - expected).abs() > 2.0_f64.max(expected * 0.002) {
        bail!("output duration {seconds} does not match complete playlist {expected}");
    }
    let streams = info["streams"].as_array().context("missing streams")?;
    if !streams.iter().any(|s| s["codec_type"] == "video") { bail!("output has no video stream"); }
    Ok(())
}

fn execute(command: &mut Command, log: &Path) -> Result<()> {
    let output = fs::OpenOptions::new().create(true).append(true).open(log)?;
    let status = command.stdin(Stdio::null()).stdout(Stdio::null()).stderr(output).status()?;
    if !status.success() { bail!("media command failed; see {}", log.display()); }
    Ok(())
}
