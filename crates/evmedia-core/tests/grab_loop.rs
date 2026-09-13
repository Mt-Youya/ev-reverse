//! The harvest loop, driven by a fixture instead of a live player.
//!
//! This is the point of the `Harvester` seam: the loop's real behaviour — resume, retry, the
//! completeness verdict, the merge — can be tested with no player, no Windows and no network.
//! Ciphertext is pre-seeded into `enc/`, so `fetch_and_decode` never has to reach for a URL.

use aes::Aes256;
use ecb::cipher::{block_padding::NoPadding, BlockEncryptMut, KeyInit};
use evmedia_contract::Reporter;
use evmedia_core::crypto::{key_from_text, mask_from_filename};
use evmedia_core::harvest::{grab, GrabOptions, Harvester};
use std::{
    collections::{BTreeMap, HashMap},
    path::{Path, PathBuf},
    time::Duration,
};

const PACKETS: usize = 4;
const SEGMENT_BYTES: usize = 188 * PACKETS;

struct Fixture {
    keys: BTreeMap<u32, (String, String)>,
    urls: HashMap<String, String>,
}

impl Harvester for Fixture {
    fn pid(&self) -> u32 {
        4242
    }
    fn keys(&self) -> anyhow::Result<BTreeMap<u32, (String, String)>> {
        Ok(self.keys.clone())
    }
    fn urls(&self) -> HashMap<String, String> {
        self.urls.clone()
    }
    fn diagnose(&self) -> String {
        "fixture".to_string()
    }
}

fn segment_name(index: u32) -> String {
    format!("119354-{index:08x}-0000-4000-8000-000000000000.ts")
}

fn segment_key(index: u32) -> String {
    format!("{index:032x}")
}

/// Deterministic plaintext that is a whole number of TS packets and of AES blocks.
fn segment_plain(index: u32) -> Vec<u8> {
    let mut data = vec![0u8; SEGMENT_BYTES];
    for packet in 0..PACKETS {
        let base = packet * 188;
        data[base] = 0x47;
        data[base + 3] = 0x10;
        for offset in 4..188 {
            data[base + offset] = ((index as usize * 31 + packet * 7 + offset) % 251) as u8;
        }
    }
    data
}

fn encrypt(plain: &[u8], key: &[u8; 32], mask: &[u8; 16]) -> Vec<u8> {
    let mut buffer = plain.to_vec();
    let length = plain.len();
    ecb::Encryptor::<Aes256>::new_from_slice(key)
        .unwrap()
        .encrypt_padded_mut::<NoPadding>(&mut buffer, length)
        .unwrap();
    for (index, byte) in buffer.iter_mut().enumerate() {
        *byte ^= mask[index % 16];
    }
    buffer
}

/// Lay out `output/enc` with the ciphertext for segments `0..count`, and describe them.
fn stage(output: &Path, count: u32) -> (Fixture, Vec<Vec<u8>>) {
    let enc = output.join("enc");
    std::fs::create_dir_all(&enc).unwrap();
    let mut keys = BTreeMap::new();
    let mut urls = HashMap::new();
    let mut plains = Vec::new();
    for index in 0..count {
        let file = segment_name(index);
        let key_text = segment_key(index);
        let plain = segment_plain(index);
        let cipher = encrypt(
            &plain,
            &key_from_text(&key_text).unwrap(),
            &mask_from_filename(&file),
        );
        std::fs::write(enc.join(&file), &cipher).unwrap();
        keys.insert(index, (file.clone(), key_text));
        urls.insert(file, format!("http://example.invalid/{index}.ts?sign=x"));
        plains.push(plain);
    }
    (Fixture { keys, urls }, plains)
}

fn scratch(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("evmedia-grab-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn options(output: &Path) -> GrabOptions {
    GrabOptions {
        output: output.to_path_buf(),
        jobs: 4,
        poll: Duration::from_millis(5),
        idle_limit: 3,
        attempts: 2,
        mp4: false,
        // The fixture has no playhead, and the default seek_to returns Ok(false) anyway.
        sweep: false,
        press_gap: Duration::from_millis(1),
    }
}

#[test]
fn a_complete_lesson_merges_to_lesson_ts() {
    let output = scratch("complete");
    let (fixture, plains) = stage(&output, 6);
    grab::run(&fixture, &options(&output), &Reporter::silent()).unwrap();

    let merged = std::fs::read(output.join("lesson.ts")).expect("lesson.ts");
    let expected: Vec<u8> = plains.concat();
    assert_eq!(merged, expected);
    assert!(!output.join("lesson.partial.ts").exists());
    // Every segment is cached decrypted, independently of the merge.
    for index in 0..6u32 {
        assert!(output.join("dec").join(format!("{index:06}.ts")).exists());
    }
}

#[test]
fn a_lesson_with_a_hole_stays_partial_and_is_never_called_lesson_ts() {
    let output = scratch("hole");
    let (mut fixture, plains) = stage(&output, 6);
    // The player never decrypted index 3, so its key does not exist.
    let (file, _) = fixture.keys.remove(&3).unwrap();
    fixture.urls.remove(&file);
    grab::run(&fixture, &options(&output), &Reporter::silent()).unwrap();

    assert!(!output.join("lesson.ts").exists(), "a hole must not produce a complete-looking file");
    let merged = std::fs::read(output.join("lesson.partial.ts")).expect("lesson.partial.ts");
    let expected: Vec<u8> = [0usize, 1, 2, 4, 5].iter().flat_map(|i| plains[*i].clone()).collect();
    assert_eq!(merged, expected);
}

#[test]
fn a_rerun_resumes_and_reproduces_the_same_bytes() {
    let output = scratch("resume");
    let (fixture, plains) = stage(&output, 5);
    grab::run(&fixture, &options(&output), &Reporter::silent()).unwrap();
    let first = std::fs::read(output.join("lesson.ts")).unwrap();

    // Delete the ciphertext cache: if the rerun needed the network it would now fail, so this
    // also proves it resumed from `dec/` rather than re-downloading.
    std::fs::remove_dir_all(output.join("enc")).unwrap();
    grab::run(&fixture, &options(&output), &Reporter::silent()).unwrap();
    let second = std::fs::read(output.join("lesson.ts")).unwrap();

    assert_eq!(first, second);
    assert_eq!(first, plains.concat());
}

#[test]
fn a_segment_that_cannot_be_decrypted_is_given_up_on_rather_than_retried_forever() {
    let output = scratch("corrupt");
    let (mut fixture, _) = stage(&output, 3);
    // Corrupt the ciphertext of index 1 so decryption fails even after a refetch attempt. The
    // URL is unreachable, so the refetch also fails — the loop must still terminate.
    let (file, key) = fixture.keys.get(&1).cloned().unwrap();
    let mut broken = std::fs::read(output.join("enc").join(&file)).unwrap();
    for byte in broken.iter_mut() {
        *byte ^= 0xa5;
    }
    std::fs::write(output.join("enc").join(&file), &broken).unwrap();
    fixture.keys.insert(1, (file, key));

    let started = std::time::Instant::now();
    grab::run(&fixture, &options(&output), &Reporter::silent()).unwrap();
    assert!(started.elapsed() < Duration::from_secs(10), "the loop must not spin on a bad segment");
    assert!(output.join("lesson.partial.ts").exists());
}
