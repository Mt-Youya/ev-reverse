use evmedia_contract::{ArtifactKind, Event, Reporter};
use evmedia_core::media::remux_mp4;
use std::{path::PathBuf, process::Command};

fn scratch(name: &str) -> PathBuf {
    let path = std::env::temp_dir().join(format!("evmedia-remux-{name}-{}", std::process::id()));
    std::fs::create_dir_all(&path).unwrap();
    path
}

#[test]
#[ignore = "requires ffmpeg with libx264 and AAC"]
fn real_audio_and_video_are_remuxed_and_verified() {
    let dir = scratch("valid");
    let ts = dir.join("input.ts");
    let status = Command::new("ffmpeg")
        .args(["-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
            "testsrc2=size=160x90:rate=10", "-f", "lavfi", "-i", "sine=frequency=440",
            "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-f", "mpegts"])
        .arg(&ts).status().unwrap();
    assert!(status.success());
    // Encrypt real media into deliberately reverse-named segments, then run the product decoder.
    use aes::Aes256;
    use ecb::cipher::{block_padding::NoPadding, BlockEncryptMut, KeyInit};
    use evmedia_core::{crypto, decode};
    let original = std::fs::read(&ts).unwrap();
    assert_eq!(original.len() % 188, 0);
    let enc = dir.join("enc");
    std::fs::create_dir_all(&enc).unwrap();
    let key_text = "0123456789abcdef0123456789abcdef";
    let key = crypto::key_from_text(key_text).unwrap();
    let mut keys = std::collections::HashMap::new();
    for (index, plain) in original.chunks(188 * 63).enumerate() {
        let file = format!("119354-{:08x}-0000-4000-8000-000000000000.ts", 10000 - index);
        let mask = crypto::mask_from_filename(&file);
        let mut cipher = plain.to_vec();
        cipher.resize((cipher.len() + 15) / 16 * 16, b'#');
        let len = cipher.len();
        ecb::Encryptor::<Aes256>::new_from_slice(&key).unwrap()
            .encrypt_padded_mut::<NoPadding>(&mut cipher, len).unwrap();
        for (position, byte) in cipher.iter_mut().enumerate() {
            *byte ^= mask[position % 16];
        }
        std::fs::write(enc.join(&file), cipher).unwrap();
        keys.insert(file, (index as u32, key_text.to_string()));
    }
    let captured = decode::build_manifest(&enc, &keys, "EVPlayer2 5.0.5 media-test").unwrap();
    let decoded = dir.join("decoded.ts");
    if decoded.exists() { std::fs::remove_file(&decoded).unwrap(); }
    decode::decode_ev(&enc, decode::EvManifest {
        tool: captured.tool, variant: String::new(), segments: captured.segments,
    }, &decoded, &Reporter::silent()).unwrap();
    assert_eq!(std::fs::read(&decoded).unwrap(), original);
    let mp4 = dir.join("lesson.mp4");
    let (reporter, events) = Reporter::capturing();
    assert!(remux_mp4(&decoded, &mp4, &reporter).unwrap().ok);
    assert!(std::fs::metadata(&mp4).unwrap().len() > 0);
    assert!(events.lock().unwrap().iter().any(|event| matches!(
        event, Event::Artifact { kind: ArtifactKind::Mp4, bytes, .. } if *bytes > 0
    )));
}

#[test]
#[ignore = "requires ffmpeg with libx264 and AAC, and ffprobe"]
fn older_codecs_are_converted_to_h264_and_aac() {
    let dir = scratch("transcode");
    let ts = dir.join("input.ts");
    assert!(Command::new("ffmpeg")
        .args(["-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
            "testsrc2=size=160x90:rate=25", "-f", "lavfi", "-i", "sine=frequency=440",
            "-t", "1", "-c:v", "mpeg2video", "-c:a", "mp2", "-f", "mpegts"])
        .arg(&ts).status().unwrap().success());
    let mp4 = dir.join("lesson.mp4");
    remux_mp4(&ts, &mp4, &Reporter::silent()).unwrap();
    let probe = Command::new("ffprobe")
        .args(["-v", "error", "-show_entries", "stream=codec_name,pix_fmt", "-of", "json"])
        .arg(mp4).output().unwrap();
    assert!(probe.status.success());
    let metadata: serde_json::Value = serde_json::from_slice(&probe.stdout).unwrap();
    let streams = metadata["streams"].as_array().unwrap();
    assert_eq!(streams[0]["codec_name"], "h264");
    assert_eq!(streams[0]["pix_fmt"], "yuv420p");
    assert_eq!(streams[1]["codec_name"], "aac");
}

#[test]
#[ignore = "requires ffmpeg"]
fn failed_remux_is_an_error_and_preserves_an_existing_output() {
    let dir = scratch("invalid");
    let ts = dir.join("input.ts");
    std::fs::write(&ts, b"not a transport stream").unwrap();
    let mp4 = dir.join("lesson.mp4");
    std::fs::write(&mp4, b"previous result").unwrap();
    let (reporter, events) = Reporter::capturing();
    assert!(remux_mp4(&ts, &mp4, &reporter).is_err());
    assert_eq!(std::fs::read(mp4).unwrap(), b"previous result");
    assert!(!events.lock().unwrap().iter().any(|event| matches!(
        event, Event::Artifact { kind: ArtifactKind::Mp4, .. }
    )));
}
