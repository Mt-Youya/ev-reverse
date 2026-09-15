#![cfg(windows)]

/// An accessible process with no player contexts still exercises the blocking HTTP client.
/// Running that client inside the old async main panicked before the first memory poll.
#[test]
fn grab_runs_without_panicking_inside_an_async_runtime() {
    let output_dir = std::env::temp_dir().join(format!("evmedia-cli-grab-{}", std::process::id()));
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_evmedia"))
        .args(["grab", "--pid", &std::process::id().to_string(), "--output"])
        .arg(output_dir)
        .args(["--lesson", "fixture", "--poll", "0", "--idle-limit", "1", "--no-sweep", "--json-events"])
        .output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    assert!(String::from_utf8_lossy(&output.stdout).contains("\"status\":\"nothing\""));
}

#[test]
fn an_output_directory_cannot_be_reassigned_to_another_lesson() {
    let dir = std::env::temp_dir().join(format!("evmedia-cli-scope-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("capture-lesson.txt"), "lesson-a").unwrap();
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_evmedia"))
        .args(["grab", "--pid", "0", "--lesson", "lesson-b", "--output"])
        .arg(dir).output().unwrap();
    assert_eq!(output.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&output.stderr).contains("output belongs to lesson lesson-a"));
}

#[test]
fn several_lessons_in_memory_require_an_explicit_choice() {
    // Keep the URLs together on a readable heap page, as the real player does.
    let urls = format!("http://cn1.evplayer.cn/lesson-a/119354-00000000-0000-4000-8000-000000000000.ts?bid=1&sid=42&sign=test\0\
        http://cn1.evplayer.cn/lesson-b/119354-11111111-1111-4111-8111-111111111111.ts?bid=1&sid=42&sign=test\0");
    let dir = std::env::temp_dir().join(format!("evmedia-cli-many-{}", std::process::id()));
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_evmedia"))
        .args(["grab", "--pid", &std::process::id().to_string(), "--output"])
        .arg(dir).output().unwrap();
    std::hint::black_box(&urls);
    let error = String::from_utf8_lossy(&output.stderr);
    assert_eq!(output.status.code(), Some(1), "{error}");
    assert!(error.contains("--lesson <UUID>") && error.contains("lesson-a") && error.contains("lesson-b"), "{error}");
}

#[test]
fn recover_with_a_missing_cached_tail_keeps_a_partial_merge() {
    let dir = std::env::temp_dir().join(format!("evmedia-cli-recover-tail-{}", std::process::id()));
    std::fs::create_dir_all(dir.join("cache")).unwrap();
    std::fs::create_dir_all(dir.join("dec")).unwrap();
    let mut plain = vec![0u8; 376];
    plain[0] = 0x47;
    plain[188] = 0x47;
    std::fs::write(dir.join("dec/000000.ts"), &plain).unwrap();
    std::fs::write(dir.join("keys.json"), r#"{
        "first.ts":{"key":"0123456789abcdef0123456789abcdef","index":0,"lesson":"fixture"},
        "tail.ts":{"key":"0123456789abcdef0123456789abcdef","index":1,"lesson":"fixture"}
    }"#).unwrap();
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_evmedia"))
        .args(["recover", "--pid", &std::process::id().to_string(), "--cache"])
        .arg(dir.join("cache")).arg("--output").arg(&dir).arg("--mp4")
        .output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    assert_eq!(std::fs::read(dir.join("lesson.partial.ts")).unwrap(), plain);
    assert!(!dir.join("lesson.ts").exists());
    assert!(!dir.join("lesson.mp4").exists());
}
