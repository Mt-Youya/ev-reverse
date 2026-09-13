//! A stand-in for a live player, shared by the harvest-loop tests.
//!
//! This is the point of the `Harvester` seam: the loop's real behaviour — resume, retry, the
//! completeness verdict, the merge, the sweep — can be tested with no player, no Windows and no
//! network. Ciphertext is pre-seeded into `enc/`, so nothing has to reach for a URL except in the
//! one test that deliberately takes it away.
//!
//! The fixture models two things about a real player that are easy to miss, and both are
//! load-bearing:
//!
//! * A candidate is only offered while its ciphertext is **on disk**. That is the real
//!   constraint — a key is found by testing it against the segment's bytes — and it is what makes
//!   the download-before-keys ordering testable rather than merely asserted.
//! * The index list and the key list are **independent**. The player holds a context for a
//!   segment it has not decrypted, and a segment with no key is exactly the gap a sweep exists to
//!   find, so `indexes()` cannot be derived from `keys()`.
#![allow(dead_code)]

use aes::Aes256;
use ecb::cipher::{block_padding::NoPadding, BlockEncryptMut, KeyInit};
use evmedia_contract::Reporter;
use evmedia_core::crypto::{key_from_text, mask_from_filename};
use evmedia_core::harvest::{GrabOptions, Harvester};
use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    io::{Read, Write},
    net::TcpListener,
    path::{Path, PathBuf},
    sync::Mutex,
    time::Duration,
};

/// Eight packets, not four. The key probe reads only the first four — 752 bytes — so anything
/// that must survive a *failed decryption* while still being solvable has to live past that.
pub const PACKETS: usize = 8;
pub const SEGMENT_BYTES: usize = 188 * PACKETS;

struct State {
    /// Filename -> index: the contexts the player is holding, whether decrypted or not.
    indexes: HashMap<String, u32>,
    /// Index -> (filename, key), for the segments whose key exists.
    keys: BTreeMap<u32, (String, String)>,
    urls: HashMap<String, String>,
    /// Where the encrypted segments live; a candidate is only offered once its bytes are here.
    enc: PathBuf,
    /// Targets the loop has asked the playhead to move to, in order.
    seeks: Vec<u32>,
    /// What `seek_to` answers. `None` models a source with no playhead at all — the trait's
    /// default, and what a real source reports when its seek keys turn out to be unbound.
    seek_reply: Option<bool>,
    /// Keys the player produces because the playhead moved onto the segment, applied on seek.
    unlock_on_seek: Vec<(u32, (String, String))>,
    /// Make the live-context source the *only* one, so the loop's use of it is observable.
    ///
    /// Off by default, which keeps every other test on the candidate path. With it on, a key can
    /// come from nowhere else: if the loop ignored `keys()` the lesson would stay empty, and that
    /// is the point.
    slot_only: bool,
}

pub struct Fixture(Mutex<State>);

impl Fixture {
    fn lock(&self) -> std::sync::MutexGuard<'_, State> {
        self.0.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    /// Drop a key, as a player that never decrypted that segment would. The context stays: it is
    /// the gap the sweep has to see.
    pub fn forget_key(&self, index: u32) -> String {
        let mut state = self.lock();
        let (file, _) = state.keys.remove(&index).expect("the fixture has that key");
        file
    }

    pub fn forget_url(&self, file: &str) {
        self.lock().urls.remove(file);
    }

    pub fn file_of(&self, index: u32) -> String {
        self.lock().keys.get(&index).expect("the fixture has that key").0.clone()
    }

    pub fn key_of(&self, index: u32) -> String {
        self.lock().keys.get(&index).expect("the fixture has that key").1.clone()
    }

    pub fn restore_key(&self, index: u32, file: String, key: String) {
        self.lock().keys.insert(index, (file, key));
    }

    pub fn seeks(&self) -> Vec<u32> {
        self.lock().seeks.clone()
    }

    pub fn set_seek_reply(&self, reply: bool) {
        self.lock().seek_reply = Some(reply);
    }

    pub fn unlock_on_seek(&self, entries: Vec<(u32, (String, String))>) {
        self.lock().unlock_on_seek = entries;
    }

    /// Answer every question from the live contexts and from nowhere else.
    pub fn use_only_live_contexts(&self) {
        self.lock().slot_only = true;
    }

    /// Repoint every URL at a real server, for the one test that has to fetch.
    pub fn retarget(&self, base: &str) {
        let mut state = self.lock();
        let rewired: Vec<(String, String)> = state
            .keys
            .iter()
            .map(|(index, (file, _))| (file.clone(), format!("{base}/{index}.ts")))
            .collect();
        for (file, url) in rewired {
            state.urls.insert(file, url);
        }
    }
}

impl Harvester for Fixture {
    fn pid(&self) -> u32 {
        4242
    }

    /// The player's live contexts: index, filename and key together, with nothing to work out.
    ///
    /// Empty unless `slot_only` is set. That is not squeamishness about the source — it is what
    /// keeps the *other* tests honest. Every one of them asserts something about the candidate
    /// path or about what happens when a key is missing, and a fixture that quietly answered
    /// every question from a complete map would let them all pass without exercising anything.
    fn keys(&self) -> BTreeMap<u32, (String, String)> {
        let state = self.lock();
        if !state.slot_only {
            return BTreeMap::new();
        }
        state.keys.clone()
    }

    /// Exactly the real shape: the key text is in memory, and it can only be *used* once the
    /// segment's bytes are somewhere to test it against.
    fn candidates(&self) -> BTreeSet<String> {
        let state = self.lock();
        if state.slot_only {
            return BTreeSet::new();
        }
        state
            .keys
            .values()
            .filter(|(file, _)| state.enc.join(file).exists())
            .map(|(_, key)| key.clone())
            .collect()
    }

    fn indexes(&self) -> HashMap<String, u32> {
        self.lock().indexes.clone()
    }

    fn urls(&self) -> HashMap<String, String> {
        self.lock().urls.clone()
    }

    fn diagnose(&self) -> String {
        "fixture".to_string()
    }

    fn seek_to(&self, target: u32, _gap: Duration, _reporter: &Reporter) -> anyhow::Result<bool> {
        let mut state = self.lock();
        state.seeks.push(target);
        let unlocks = state.unlock_on_seek.clone();
        for (index, entry) in unlocks {
            state.keys.insert(index, entry);
        }
        Ok(state.seek_reply.unwrap_or(false))
    }
}

pub fn segment_name(index: u32) -> String {
    format!("119354-{index:08x}-0000-4000-8000-000000000000.ts")
}

pub fn segment_key(index: u32) -> String {
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
pub fn stage(output: &Path, count: u32) -> (Fixture, Vec<Vec<u8>>) {
    let enc = output.join("enc");
    std::fs::create_dir_all(&enc).unwrap();
    let mut indexes = HashMap::new();
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
        indexes.insert(file.clone(), index);
        keys.insert(index, (file.clone(), key_text));
        urls.insert(file, format!("http://example.invalid/{index}.ts?sign=x"));
        plains.push(plain);
    }
    let fixture = Fixture(Mutex::new(State {
        indexes,
        keys,
        urls,
        enc,
        seeks: Vec::new(),
        seek_reply: None,
        unlock_on_seek: Vec::new(),
        slot_only: false,
    }));
    (fixture, plains)
}

/// A minimal HTTP/1.1 server that serves the staged ciphertext from memory.
///
/// The download path cannot be reached with the ciphertext already on disk, and every other test
/// deliberately starts that way. Rather than reaching for the network, this serves the exact
/// bytes the fixtures encrypt, so the test stays hermetic and fast.
pub fn serve(bodies: HashMap<String, Vec<u8>>) -> (String, std::thread::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind a loopback port");
    let port = listener.local_addr().unwrap().port();
    let handle = std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { break };
            let mut head = Vec::new();
            let mut byte = [0u8; 1];
            while !head.ends_with(b"\r\n\r\n") {
                match stream.read(&mut byte) {
                    Ok(1) => head.push(byte[0]),
                    _ => break,
                }
            }
            let request = String::from_utf8_lossy(&head).into_owned();
            let path = request.split_whitespace().nth(1).unwrap_or_default();
            match bodies.get(path) {
                Some(body) => {
                    let header = format!(
                        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                        body.len()
                    );
                    let _ = stream.write_all(header.as_bytes());
                    let _ = stream.write_all(body);
                }
                None => {
                    let _ = stream.write_all(
                        b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
                    );
                }
            }
        }
    });
    (format!("http://127.0.0.1:{port}"), handle)
}

pub fn scratch(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("evmedia-grab-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

pub fn options(output: &Path) -> GrabOptions {
    GrabOptions {
        output: output.to_path_buf(),
        jobs: 4,
        poll: Duration::from_millis(5),
        idle_limit: 3,
        attempts: 2,
        mp4: false,
        // Off by default: `seek_to` is the fixture default of Ok(false), so a test that wants a
        // sweep has to ask for one and say what the playhead answers.
        sweep: false,
        press_gap: Duration::from_millis(1),
    }
}
