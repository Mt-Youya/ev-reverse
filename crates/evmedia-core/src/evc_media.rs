//! EVC changes H.264 CABAC context selection. Remuxing cannot make that stream standard H.264.
//! A small, source-built FFmpeg decoder restores pixels; the regular FFmpeg encodes them losslessly.
use crate::full_export::Options;
use crate::evc_slots::{self, EvcLease};
use crate::verified_media::media_stage;
use anyhow::{bail, Context, Result};
use evmedia_contract::Reporter;
use std::{
    fs,
    io,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

/// The ordinary decoder cannot decode EVC, so the CPU always reconstructs the source frames.
/// The expensive *encoding* half can use NVENC when the selected FFmpeg and driver provide it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Encoder {
    Nvenc,
    X264,
}

impl Encoder {
    fn args(self) -> &'static [&'static str] {
        match self {
            // These are FFmpeg's documented lossless NVENC settings. `constqp=0` avoids a
            // quality/speed trade-off; the subsequent full decode check still protects publish.
            Self::Nvenc => &[
                "-map", "0:v:0", "-c:v", "h264_nvenc", "-preset", "lossless", "-tune",
                "lossless", "-rc", "constqp", "-qp", "0", "-fps_mode", "passthrough",
            ],
            Self::X264 => &[
                "-map", "0:v:0", "-c:v", "libx264", "-preset", "fast", "-crf", "0",
                "-fps_mode", "passthrough",
            ],
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::Nvenc => "GPU（NVIDIA NVENC）",
            Self::X264 => "CPU（libx264）",
        }
    }
}

/// Ask the exact FFmpeg chosen by the user, rather than assuming the packaged build is in PATH.
fn nvenc_available(ffmpeg: &str) -> bool {
    Command::new(ffmpeg)
        .args(["-hide_banner", "-h", "encoder=h264_nvenc"])
        .output()
        .is_ok_and(|output| output.status.success())
}

fn decoder() -> Result<PathBuf> {
    let path = std::env::var_os("EVMEDIA_EVC_DECODER")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            std::env::current_exe()
                .unwrap_or_default()
                .with_file_name(if cfg!(windows) {
                    "evmedia-ffmpeg.exe"
                } else {
                    "evmedia-ffmpeg"
                })
        });
    if !path.is_file() {
        bail!(
            "视频解码失败；缺少兼容解码器 {}，请运行 tools/evc-decoder/build.ps1",
            path.display()
        );
    }
    Ok(path)
}

fn input(decoder: &Path, ts: &Path, key: u32, threads: &str) -> Command {
    let mut command = Command::new(decoder);
    command
        .args([
            "-v",
            "error",
            "-nostdin",
            "-xerror",
            "-err_detect",
            "explode",
            "-threads",
            threads,
            "-is_evc",
        ])
        .arg(key.to_string())
        .arg("-i")
        .arg(ts);
    command
}

fn find_context(decoder: &Path, probe: &Path, reporter: &Reporter, workers: usize) -> Result<u32> {
    // The descriptor can say evc_val=0 even for protected lessons. Only accept a unique key
    // that decodes three frames without concealment; validate the entire lesson afterwards.
    let candidates = std::thread::scope(|scope| -> Result<Vec<u32>> {
        let handles: Vec<_> = (0..workers)
            .map(|worker| {
                scope.spawn(move || -> Result<Vec<u32>> {
                    let mut found = Vec::new();
                    for key in (1 + worker..=512).step_by(workers) {
                        let key = key as u32;
                        if reporter.stopped() {
                            bail!("export stopped");
                        }
                        let result = input(decoder, probe, key, "1")
                            .args(["-map", "0:v:0", "-an", "-frames:v", "3", "-f", "null", "-"])
                            .output()
                            .context("probe EVC decoder")?;
                        if result.status.success() && result.stderr.is_empty() {
                            found.push(key);
                        }
                    }
                    Ok(found)
                })
            })
            .collect();
        let mut found = Vec::new();
        for handle in handles {
            found.extend(
                handle
                    .join()
                    .map_err(|_| anyhow::anyhow!("EVC probe thread failed"))??,
            );
        }
        Ok(found)
    })?;
    if candidates.len() != 1 {
        bail!(
            "无法唯一确定视频保护参数（{} 个候选）；保留日志，未发布成品",
            candidates.len()
        );
    }
    Ok(candidates[0])
}

/// Replace a failed remux's partial file, never an existing finished output.
pub fn repair(
    ts: &Path,
    partial: &Path,
    options: &Options,
    work: &Path,
    seconds: f64,
    reporter: &Reporter,
) -> Result<u32> {
    let decoder = decoder()?;
    // Hold a bounded lease across probing, decoding and encoding. The GUI passes its publish-pool
    // size, and per-repair CPU decoder/probe workers are reduced to keep the host responsive.
    let slots = evc_slots::slots();
    let _lease = EvcLease::acquire(work, slots, reporter)?;
    media_stage(reporter, "正在检测视频兼容参数，尚未生成成品");
    // enc/ holds encrypted downloads, not decoder-ready TS segments. Probe the
    // decrypted, ordered stream; find_context only decodes its first three frames.
    let key = find_context(&decoder, ts, reporter, evc_slots::probe_workers(slots))?;
    let log = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(work.join("evc-media.log"))?;
    let repaired_video = work.join("evc-video.mkv");
    let progress = work.join("evc-encoding.progress");
    for path in [&repaired_video, &progress, partial] {
        match fs::remove_file(path) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(e.into()),
        }
    }
    let encoder = if nvenc_available(&options.ffmpeg) {
        Encoder::Nvenc
    } else {
        Encoder::X264
    };
    media_stage(reporter, format!(
        "兼容解码参数 {key} 已验证，正在使用{}无损转换视频（0%，最多 {slots} 个并行），尚未生成成品",
        encoder.label(),
    ));
    let threads = evc_slots::decode_threads(slots);
    if let Err(error) = encode_lossless(&decoder, ts, key, options, &repaired_video, &progress, seconds, reporter, &log, encoder, threads) {
        if encoder != Encoder::Nvenc || reporter.stopped() {
            return Err(error);
        }
        // Driver availability can change after capability discovery. Start the pipe again with
        // x264 rather than publishing a partial GPU output.
        media_stage(reporter, "GPU 无损编码失败，正在回退 CPU 无损转换（0%），尚未生成成品");
        encode_lossless(&decoder, ts, key, options, &repaired_video, &progress, seconds, reporter, &log, Encoder::X264, threads)?;
    }
    mux_repaired_video(&repaired_video, ts, partial, options, log)?;
    Ok(key)
}

fn encode_lossless(
    decoder: &Path,
    ts: &Path,
    key: u32,
    options: &Options,
    repaired_video: &Path,
    progress: &Path,
    seconds: f64,
    reporter: &Reporter,
    log: &fs::File,
    encoder: Encoder,
    threads: usize,
) -> Result<()> {
    for path in [repaired_video, progress] {
        match fs::remove_file(path) {
            Ok(()) => {}
            Err(e) if e.kind() == io::ErrorKind::NotFound => {}
            Err(e) => return Err(e.into()),
        }
    }
    let threads = threads.to_string();
    let mut decode = input(decoder, ts, key, &threads);
    decode
        .args([
            "-map", "0:v:0", "-c:v", "rawvideo", "-vsync", "0", "-f", "nut", "pipe:1",
        ])
        .stdout(Stdio::piped())
        .stderr(log.try_clone()?);
    let mut decode_child = decode.spawn().context("start EVC decode")?;
    let decoded = decode_child.stdout.take().context("capture EVC frames")?;
    let mut encode = Command::new(&options.ffmpeg);
    encode
        .args(["-v", "error", "-nostdin", "-y", "-xerror", "-i"])
        .arg("pipe:0")
        .args(encoder.args())
        .args(["-progress"]).arg(progress)
        .arg(repaired_video)
        .stdin(decoded)
        .stdout(Stdio::null())
        .stderr(log.try_clone()?);
    let mut encode_child = match encode.spawn() {
        Ok(child) => child,
        Err(error) => {
            let _ = decode_child.kill();
            let _ = decode_child.wait();
            return Err(error).context("start lossless video encode");
        }
    };
    let mut last_percent = 0;
    let encode_status = loop {
        if reporter.stopped() {
            let _ = encode_child.kill();
            let _ = decode_child.kill();
            let _ = encode_child.wait();
            let _ = decode_child.wait();
            bail!("export stopped");
        }
        if let Some(status) = encode_child.try_wait()? { break status; }
        if let Ok(text) = fs::read_to_string(&progress) {
            let elapsed = text.lines().rev().find_map(|line|
                line.strip_prefix("out_time_us=").and_then(|v| v.parse::<f64>().ok()));
            if let Some(elapsed) = elapsed {
                let percent = (elapsed / 1_000_000.0 / seconds * 100.0).clamp(0.0, 99.0) as u32;
                if percent > last_percent {
                    media_stage(reporter, format!("正在使用{}转换视频（{percent}%），完成后还需校验", encoder.label()));
                    last_percent = percent;
                }
            }
        }
        reporter.sleep(std::time::Duration::from_millis(500));
    };
    let decode_status = decode_child.wait().context("wait EVC decode")?;
    if !decode_status.success() {
        bail!(
            "兼容解码失败；参见 {}",
            options.work.join("evc-media.log").display()
        );
    }
    if !encode_status.success() {
        bail!("{}无损视频编码失败；参见 {}", encoder.label(), options.work.join("evc-media.log").display());
    }
    Ok(())
}

fn mux_repaired_video(video: &Path, ts: &Path, partial: &Path, options: &Options,
                      log: fs::File) -> Result<()> {
    let mut mux = Command::new(&options.ffmpeg);
    // Segment boundaries can repeat AAC timestamps. During stream-copy, let the
    // muxer make DTS monotonic; -xerror turns this repairable warning into failure.
    // Pixel decoding, encoding and final full A/V validation remain strict.
    mux.args(["-v", "warning", "-nostdin", "-y", "-i"])
        .arg(video)
        .args(["-i"])
        .arg(ts)
        .args([
            "-map",
            "0:v:0",
            "-map",
            "1:a?",
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-shortest",
        ]);
    if partial
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("mp4"))
    {
        mux.args(["-movflags", "+faststart"]);
    }
    let status = mux
        .arg(partial)
        .stdout(Stdio::null())
        .stderr(log)
        .status()
        .context("mux repaired video and original audio")?;
    if !status.success() {
        bail!(
            "修复后的视频封装失败；参见 {}",
            options.work.join("evc-media.log").display()
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nvenc_uses_lossless_options() {
        let args = Encoder::Nvenc.args();
        assert!(args.windows(2).any(|pair| pair == ["-c:v", "h264_nvenc"]));
        assert!(args.windows(2).any(|pair| pair == ["-preset", "lossless"]));
        assert!(args.windows(2).any(|pair| pair == ["-qp", "0"]));
    }

    #[test]
    fn cpu_fallback_stays_lossless() {
        let args = Encoder::X264.args();
        assert!(args.windows(2).any(|pair| pair == ["-c:v", "libx264"]));
        assert!(args.windows(2).any(|pair| pair == ["-crf", "0"]));
    }

    #[test]
    #[ignore = "requires EVMEDIA_MUX_WORK with lesson.ts and evc-video.mkv"]
    fn overlapping_audio_timestamps_can_be_muxed_and_decoded() -> Result<()> {
        let source = PathBuf::from(std::env::var_os("EVMEDIA_MUX_WORK").context("sample work")?);
        let work = std::env::temp_dir().join(format!("evmedia-mux-{}", std::process::id()));
        fs::create_dir_all(&work)?;
        let options = Options { output: work.join("recovered.mp4"), work: work.clone(), cache: None,
            jobs: 1, ffmpeg: "ffmpeg".into(), ffprobe: "ffprobe".into() };
        let log = fs::File::create(work.join("evc-media.log"))?;
        mux_repaired_video(&source.join("evc-video.mkv"), &source.join("lesson.ts"),
            &options.output, &options, log)?;
        let result = Command::new(&options.ffmpeg).args(["-v", "error", "-xerror", "-i"])
            .arg(&options.output).args(["-map", "0:a:0", "-f", "null", "-"]).output()?;
        assert!(result.status.success(), "{}", String::from_utf8_lossy(&result.stderr));
        fs::remove_file(&options.output)?;
        fs::remove_file(work.join("evc-media.log"))?;
        fs::remove_dir(work)?;
        Ok(())
    }
}
