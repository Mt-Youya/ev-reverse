//! Publish MP4/MKV only after matching VOD duration and decoding all audio/video streams.
use crate::{full_export::Options, vod::Vod};
use anyhow::{bail, Context, Result};
use evmedia_contract::{Event, Reporter, Stage, StageState};
use serde_json::{json, Value};
use std::{fs, path::Path, process::{Command, Stdio}, time::Duration};

pub fn publish(ts: &Path, vod: &Vod, options: &Options, work: &Path, reporter: &Reporter) -> Result<()> {
    let output = &options.output;
    if let Some(parent) = output.parent().filter(|p| !p.as_os_str().is_empty()) {
        fs::create_dir_all(parent)?;
    }
    let extension = output.extension().context("missing container")?.to_string_lossy();
    let stem = output.file_stem().context("missing filename")?.to_string_lossy();
    // Unverified media belongs in scratch space, never beside finished videos.
    fs::create_dir_all(work)?;
    let partial = work.join(format!("{stem}.partial.{extension}"));
    let log = work.join("media.log");
    for name in [format!("report-{extension}.json"), "report.json".into()] {
        match fs::remove_file(work.join(name)) {
            Ok(()) => {},
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {},
            Err(e) => return Err(e.into()),
        }
    }
    media_stage(reporter, "正在处理视频，尚未生成成品");
    let mut mux = Command::new(&options.ffmpeg);
    mux.args(["-v", "warning", "-nostdin", "-y", "-i"]).arg(ts)
        .args(["-map", "0:v:0", "-map", "0:a?", "-c", "copy"]);
    if extension == "mp4" { mux.args(["-movflags", "+faststart"]); }
    execute(mux.arg(&partial), &log)?;
    if reporter.stopped() { bail!("export stopped"); }
    media_stage(reporter, "完整解码校验视频和音频，尚未生成成品");
    let mut evc_context = None;
    if validate_decode(&partial, options, work, &log).is_err() {
        if reporter.stopped() { bail!("export stopped"); }
        evc_context = Some(crate::evc_media::repair(ts, &partial, options, work, vod.seconds, reporter)?);
        media_stage(reporter, "完整解码校验修复后的视频和音频，尚未生成成品");
        validate_decode(&partial, options, work, &log)?;
    }
    let probe = Command::new(&options.ffprobe).args(["-v", "error", "-show_streams", "-show_format", "-of", "json"])
        .arg(&partial).output().context("run ffprobe")?;
    if !probe.status.success() { bail!("ffprobe failed"); }
    let info: Value = serde_json::from_slice(&probe.stdout)?;
    let seconds: f64 = info["format"]["duration"].as_str().context("missing output duration")?.parse()?;
    validate_probe(&info, seconds, vod.seconds)?;
    if reporter.stopped() { bail!("export stopped"); }
    let report = json!({"status":"complete", "segments":vod.names.len(),
        "playlist_seconds":vod.seconds, "output_seconds":seconds, "full_decode_verified":true,
        "evc_context":evc_context, "lossless_video_conversion":evc_context.is_some(),
        "output":output, "bytes":fs::metadata(&partial)?.len(), "streams":info["streams"]});
    // Work and output may be on different volumes. Copy only verified bytes to a
    // non-media filename on the destination volume, then publish atomically.
    let publishing = output.with_file_name(format!(".{stem}.{extension}.publishing"));
    fs::copy(&partial, &publishing)?;
    for attempt in 0..=120 {
        match fs::rename(&publishing, output) {
            Ok(()) => break,
            Err(e) if cfg!(windows) && e.raw_os_error() == Some(32) && attempt < 120 => {
                std::thread::sleep(Duration::from_millis(250));
            }
            Err(e) => return Err(e.into()),
        }
    }
    let _ = fs::remove_file(&partial);
    fs::write(work.join(format!("report-{extension}.json")), serde_json::to_vec_pretty(&report)?)?;
    reporter.info(format!("整课导出完成：{}（{seconds:.3} 秒，{} 段）", output.display(), vod.names.len()));
    Ok(())
}

pub(crate) fn media_stage(reporter: &Reporter, detail: impl Into<String>) {
    let detail = detail.into();
    reporter.info(&detail);
    reporter.event(&Event::Stage { name: Stage::Remux, state: StageState::Begin, detail });
}

fn validate_decode(partial: &Path, options: &Options, work: &Path, log: &Path) -> Result<()> {
    execute(Command::new(&options.ffmpeg).args(["-v", "error", "-nostdin", "-xerror", "-threads", "4", "-i"])
        .arg(partial).args(["-map", "0:v", "-map", "0:a?", "-progress"])
        .arg(work.join("validation.progress")).args(["-nostats", "-f", "null", "-"]), log)
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
