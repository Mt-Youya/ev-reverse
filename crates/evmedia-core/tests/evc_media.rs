//! Opt-in regression with a local authorized EVC sample; no lesson bytes or keys are committed.
use evmedia_contract::Reporter;
use evmedia_core::{full_export::Options, verified_media, vod::Vod};
use std::{path::PathBuf, process::Command};

#[test]
#[ignore = "requires EVMEDIA_EVC_SAMPLE and the built EVC decoder"]
fn protected_video_is_recovered_and_fully_verified() {
    let sample = PathBuf::from(std::env::var_os("EVMEDIA_EVC_SAMPLE").expect("sample TS"));
    let work = std::env::var_os("EVMEDIA_EVC_WORK")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            std::env::temp_dir().join(format!("evmedia-evc-test-{}", std::process::id()))
        });
    std::fs::create_dir_all(&work).unwrap();
    // Production work directories contain ciphertext in enc/. It must never be
    // selected as decoder input, even though its files have a .ts extension.
    if std::env::var_os("EVMEDIA_EVC_WORK").is_none() {
        std::fs::create_dir_all(work.join("enc")).unwrap();
        std::fs::write(work.join("enc/encrypted-cache.ts"), [0x91; 1024]).unwrap();
    }
    let output = std::env::var_os("EVMEDIA_EVC_OUTPUT")
        .map(PathBuf::from)
        .unwrap_or_else(|| work.join("out/recovered.mp4"));
    let options = Options {
        output,
        work: work.clone(),
        cache: None,
        jobs: 1,
        ffmpeg: "ffmpeg".into(),
        ffprobe: "ffprobe".into(),
    };
    let vod = if let Some(playlist) = std::env::var_os("EVMEDIA_EVC_PLAYLIST") {
        Vod::parse(&std::fs::read_to_string(playlist).unwrap()).unwrap()
    } else {
        Vod {
            names: vec!["sample.ts".into()],
            seconds: 16.666667,
        }
    };
    let (reporter, events) = Reporter::capturing();
    let leaked_partial = std::thread::scope(|scope| {
        let job = scope.spawn(|| verified_media::publish(
            &sample, &vod, &options, &work, &reporter));
        let partial = options.output.with_file_name(format!("{}.partial.mp4",
            options.output.file_stem().unwrap().to_string_lossy()));
        let mut leaked = false;
        while !job.is_finished() {
            leaked |= partial.exists();
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        job.join().unwrap().unwrap();
        leaked
    });
    assert!(!leaked_partial, "unverified MP4 appeared in the finished-output directory");
    assert!(events.lock().unwrap().iter().any(|event| matches!(event,
        evmedia_contract::Event::Stage { detail, .. } if detail.contains("正在转换视频"))));
    let result = Command::new("ffmpeg")
        .args(["-v", "error", "-xerror", "-i"])
        .arg(&options.output)
        .args(["-f", "null", "-"])
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    let report: serde_json::Value =
        serde_json::from_slice(&std::fs::read(work.join("report-mp4.json")).unwrap()).unwrap();
    assert_eq!(report["full_decode_verified"], true);
    let expected: u32 = std::env::var("EVMEDIA_EVC_CONTEXT")
        .unwrap_or_else(|_| "18".into()).parse().unwrap();
    assert_eq!(report["evc_context"], expected);
    if std::env::var_os("EVMEDIA_EVC_KEEP").is_none() {
        std::fs::remove_dir_all(work).unwrap();
    }
}
