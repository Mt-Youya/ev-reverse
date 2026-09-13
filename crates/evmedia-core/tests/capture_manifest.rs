//! `decode::build_manifest`, which is the whole of `capture-ev` except for reading a player.
//!
//! Testing it here rather than through the command is the point of the split: the command needs a
//! live EVPlayer2, and this does not — so the part of it that can be wrong in a quiet way, a
//! missing segment or a gap in the index order, is the part that is covered.

use evmedia_core::crypto::{hex_lower, mask_from_filename, sha256_hex};
use evmedia_core::decode::build_manifest;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

const TOOL: &str = "test";

fn name(index: u32) -> String {
    format!("119354-{index:08x}-0000-4000-8000-000000000000.ts")
}

fn key(index: u32) -> String {
    format!("{index:032x}")
}

fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("evmedia-capture-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

/// Stage `count` segments on disk and describe them the way a player would.
fn stage(dir: &Path, count: u32) -> HashMap<String, (u32, String)> {
    let mut keys = HashMap::new();
    for index in 0..count {
        let file = name(index);
        // The contents do not matter beyond being stable; the manifest records their digest.
        std::fs::write(dir.join(&file), format!("ciphertext-{index}")).unwrap();
        keys.insert(file, (index, key(index)));
    }
    keys
}

#[test]
fn a_complete_lesson_becomes_a_manifest_in_index_order() {
    let dir = scratch("complete");
    let keys = stage(&dir, 4);

    let manifest = build_manifest(&dir, &keys, TOOL).unwrap();
    assert_eq!(manifest.tool, TOOL);
    assert_eq!(manifest.segment_count, 4);
    // Ordered by index, not by whatever order the map happened to yield.
    assert_eq!(manifest.segments.iter().map(|s| s.index).collect::<Vec<_>>(), vec![0, 1, 2, 3]);
    for segment in &manifest.segments {
        assert_eq!(segment.key_hex, hex_lower(key(segment.index).as_bytes()));
        assert_eq!(segment.xor_mask_hex, hex_lower(&mask_from_filename(&segment.file)));
        let bytes = std::fs::read(dir.join(&segment.file)).unwrap();
        assert_eq!(segment.encrypted_sha256, sha256_hex(&bytes));
    }
}

#[test]
fn a_segment_with_no_key_is_refused_rather_than_omitted() {
    let dir = scratch("missing");
    let mut keys = stage(&dir, 3);
    keys.remove(&name(1));

    let error = build_manifest(&dir, &keys, TOOL).expect_err("a missing key must fail");
    let text = error.to_string();
    assert!(text.contains("no key for 1 of 3"), "unhelpful error: {text}");
    assert!(text.contains(&name(1)), "the error should name the segment: {text}");
}

#[test]
fn a_gap_in_the_index_order_is_refused() {
    let dir = scratch("gap");
    let mut keys = stage(&dir, 3);
    // Every segment has a key, but they are not 0..n. `decode-ev` assembles by position, so this
    // would produce a file of the wrong length without failing anywhere downstream.
    keys.insert(name(1), (7, key(1)));

    let error = build_manifest(&dir, &keys, TOOL).expect_err("a gap must fail");
    assert!(error.to_string().contains("contiguous"), "unhelpful error: {error}");
}

#[test]
fn an_input_with_no_segments_is_refused() {
    let dir = scratch("empty");
    let error = build_manifest(&dir, &HashMap::new(), TOOL).expect_err("an empty input must fail");
    assert!(error.to_string().contains("no .ts segments"), "unhelpful error: {error}");
}
