use evmedia_core::{playlist::{Playlist, PlaylistEntry}, vod::Vod};

fn name(i: usize) -> String {
    format!("119354-aaaaaaaa-0000-4000-8000-{i:012x}.ts")
}

fn playlist(count: usize) -> String {
    let mut text = "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n".to_string();
    for i in 0..count {
        text.push_str(&format!("#EXTINF:8.333333,\n{}\n", name(i)));
    }
    text.push_str("#EXT-X-ENDLIST\n");
    text
}

#[test]
fn rejects_windows_duplicates_missing_durations_and_unsafe_names() {
    let valid = playlist(2);
    for text in [
        valid.replace("#EXT-X-ENDLIST", ""),
        valid.replace("MEDIA-SEQUENCE:0", "MEDIA-SEQUENCE:5"),
        valid.replace(&name(1), &name(0)),
        valid.replace("#EXTINF:8.333333,\n", ""),
        valid.replace(&name(1), "..\\video.ts"),
        valid.replace("8.333333", "NaN"),
    ] {
        assert!(Vod::parse(&text).is_err());
    }
    let vod = Vod::parse(&valid).unwrap();
    assert_eq!(vod.names, vec![name(0), name(1)]);
    assert!((vod.seconds - 16.666666).abs() < 1e-6);
}

#[test]
fn batches_use_window_indexes_and_exact_complete_membership() {
    let vod = Vod::parse(&playlist(205)).unwrap();
    for (offset, length) in [(0, 100), (100, 100), (200, 5)] {
        let mut reply = Playlist { d_p: "http://example.test".into(), k_l: (0..length).rev()
            .map(|i| PlaylistEntry { idx: i as u32, sf: format!("/{}?sign=fake", name(offset + i)), tk: "0".repeat(32) }).collect() };
        vod.validate_batch(offset, &reply).unwrap();
        reply.k_l.pop();
        assert!(vod.validate_batch(offset, &reply).is_err());
    }
}

#[test]
fn session_uses_matching_video_even_when_another_video_was_requested_later() {
    use evmedia_core::full_export::Session;
    let vod = Vod::parse(&playlist(2)).unwrap();
    let lines = [
        serde_json::json!({"headers":{"authorization":"fake-token"}}),
        serde_json::json!({"input":format!("app_version=5.0.5&evs_playkey=matching&ts_liststr=0|0|{}", name(1))}),
        serde_json::json!({"input":format!("app_version=5.0.5&evs_playkey=other&ts_liststr=0|0|{}", name(9))}),
    ].iter().map(|v|v.to_string()).collect::<Vec<_>>().join("\n");
    let session = Session::parse(&lines, &vod).unwrap();
    assert_eq!(session.playkey, "matching");
    assert_eq!(session.token, "fake-token");
    assert!(Session::parse("{}", &vod).is_err());
}

#[test]
fn validation_rejects_audio_only_and_truncated_output() {
    use evmedia_core::verified_media::validate_probe;
    let video = serde_json::json!({"streams":[{"codec_type":"video"},{"codec_type":"audio"}]});
    assert!(validate_probe(&video, 1908.923, 1908.900).is_ok());
    assert!(validate_probe(&video, 1800.0, 1908.900).is_err());
    assert!(validate_probe(&video, f64::NAN, 1908.900).is_err());
    assert!(validate_probe(&serde_json::json!({"streams":[{"codec_type":"audio"}]}), 10.0, 10.0).is_err());
}
