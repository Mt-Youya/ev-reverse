//! Turning candidate keys into open segments.
//!
//! While the player has a segment decrypted for playback, its key sits in the heap as **32 hex
//! characters** of plain text. The key never crosses the network -- the API hands out `tk`, which
//! is not the key -- so the process's own memory is the only place it exists.
//!
//! Which segment a key belongs to needs no knowledge of memory layout at all: try the key on the
//! segment's bytes and see whether MPEG-TS falls out. That check is what makes this reliable --
//! struct offsets change between builds, AES does not.

use crate::crypto::{decrypt, key_from_text, mask_from_filename};
use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::io::Read;
use std::path::Path;

/// One segment's key, plus the two things needed to place it: which lesson it belongs to, and
/// where inside that lesson it sits.
///
/// The lesson is not decoration. Segment indexes restart at 0 for every lesson, so merging by
/// index alone silently stitches two lessons together — the filename is a globally unique UUID,
/// the index is not. That failure is quiet: the merge succeeds and produces a file of the wrong
/// length.
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
pub struct KeyEntry {
    pub key: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub index: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub lesson: Option<String>,
}

/// Segment filename -> its key entry.
///
/// Keyed by filename because that is the one identifier unique across lessons, so the library
/// stays valid when the player is restarted or moved to a different lesson.
pub type Library = BTreeMap<String, KeyEntry>;

/// Reads either the current entry shape or the older `filename -> "key"` one, so a library
/// written by an earlier build still loads instead of being silently thrown away.
#[derive(Deserialize)]
#[serde(untagged)]
enum RawEntry {
    Plain(String),
    Full(KeyEntry),
}

impl From<RawEntry> for KeyEntry {
    fn from(raw: RawEntry) -> Self {
        match raw {
            RawEntry::Plain(key) => KeyEntry { key, index: None, lesson: None },
            RawEntry::Full(entry) => entry,
        }
    }
}

/// How much of a segment to read when testing a key.
///
/// Must be a multiple of both 188 and 16: 188 is 4·47, so 188·4 = 752 is the smallest number
/// satisfying both. **Do not use 376.** It is 2·188 but not a multiple of 16, so AES rejects it
/// as unaligned -- and once that error is swallowed, every key looks like a wrong key and the
/// whole round of testing comes back as a false negative.
pub const PROBE_BYTES: usize = 188 * 4;

/// Does this candidate key open this segment?
///
/// `head` should be [`PROBE_BYTES`] long. Anything shorter returns false rather than erroring,
/// because a directory mid-download has half-written files in it.
pub fn opens(filename: &str, key_text: &str, head: &[u8]) -> bool {
    if head.len() < PROBE_BYTES {
        return false;
    }
    let Ok(key) = key_from_text(key_text) else {
        return false;
    };
    let mask = mask_from_filename(filename);
    decrypt(head, &key, &mask, filename).is_ok()
}

/// Find the key for every segment in `dir` that one of `candidates` opens, skipping anything
/// already in `skip`.
///
/// Returns filename -> key, holding only segments that actually opened. One key may open several
/// segments and each is recorded: keys are genuinely reused across some segments.
///
/// The `skip` set is what makes a sweep affordable. A sweep re-tests on every round, so testing
/// all candidates against all segments each time would cost tens of millions of AES calls per
/// round; segments already solved cannot become unsolved, and only the handful of *new* candidates
/// can open anything, so each round only pays for (new candidates x unsolved segments).
pub fn recover(
    dir: &Path,
    candidates: &BTreeSet<String>,
    skip: &Library,
) -> Result<BTreeMap<String, String>> {
    let mut out = BTreeMap::new();
    for entry in std::fs::read_dir(dir)? {
        let path = entry?.path();
        if path.extension().and_then(|value| value.to_str()) != Some("ts") {
            continue;
        }
        let Some(filename) = path.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        if skip.contains_key(filename) {
            continue;
        }
        // Reading the whole file would be wasteful: a wrong key is rejected by the first four
        // packets, and the file may be large.
        let Ok(mut handle) = std::fs::File::open(&path) else {
            continue;
        };
        let mut head = Vec::with_capacity(PROBE_BYTES);
        if handle.by_ref().take(PROBE_BYTES as u64).read_to_end(&mut head).is_err() {
            continue;
        }
        for candidate in candidates {
            if opens(filename, candidate, &head) {
                out.insert(filename.to_string(), candidate.clone());
                break;
            }
        }
    }
    Ok(out)
}

/// Lesson id used for entries that carry no lesson of their own — an older library, or a segment
/// the API never named. They still decrypt and still merge, just among themselves.
pub const UNKNOWN_LESSON: &str = "unknown";

/// Read a key library from disk.
///
/// A missing file is an empty library rather than an error: the first run has nothing to resume
/// from, and that is an ordinary state, not a failure. The library exists so keys already
/// recovered survive both a player restart and an interrupted sweep.
pub fn load_library(path: &Path) -> Library {
    std::fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<BTreeMap<String, RawEntry>>(&bytes).ok())
        .map(|raw| raw.into_iter().map(|(file, entry)| (file, entry.into())).collect())
        .unwrap_or_default()
}

/// Write the key library, creating the parent directory if needed.
pub fn save_library(path: &Path, library: &Library) -> Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(path, serde_json::to_vec_pretty(library)?)?;
    Ok(())
}

/// One lesson's segments, ready to decrypt and merge.
pub struct LessonGroup {
    /// Playback index -> (filename, key).
    pub entries: BTreeMap<u32, (String, String)>,
    /// True when every segment here carries a real index read from the player.
    ///
    /// False means the indexes were assigned positionally as a fallback, which says nothing about
    /// playback order. Such a group can still be decrypted, but merging it produces a file of the
    /// wrong length and duration without anything failing — so callers must not merge it. This is
    /// the ordinary state for segments whose URLs the player has already released: keys are never
    /// dropped, URLs are.
    pub placed: bool,
}

/// Split the library into one group per lesson.
///
/// Merging must happen per lesson, never across the whole library: indexes restart at 0 for each
/// lesson, so a global index space would overwrite one lesson's segments with another's.
pub fn group_by_lesson(library: &Library) -> BTreeMap<String, LessonGroup> {
    let mut groups: BTreeMap<String, Vec<(&String, &KeyEntry)>> = BTreeMap::new();
    for (file, entry) in library {
        let lesson = entry.lesson.clone().unwrap_or_else(|| UNKNOWN_LESSON.to_string());
        groups.entry(lesson).or_default().push((file, entry));
    }

    let mut out = BTreeMap::new();
    for (lesson, mut items) in groups {
        items.sort_by(|left, right| left.0.cmp(right.0));
        let placed = items.iter().all(|(_, entry)| entry.index.is_some());
        let mut used: BTreeSet<u32> = items.iter().filter_map(|(_, entry)| entry.index).collect();
        let mut next = 0u32;
        let mut entries = BTreeMap::new();
        for (file, entry) in items {
            let index = match entry.index {
                Some(index) => index,
                None => {
                    while used.contains(&next) {
                        next += 1;
                    }
                    used.insert(next);
                    next
                }
            };
            entries.insert(index, (file.clone(), entry.key.clone()));
        }
        out.insert(lesson, LessonGroup { entries, placed });
    }
    out
}

/// Decrypt the segments `keys` covers, writing `<index:06>.ts` into `out`.
///
/// Reads from the player's cache and never touches the network: the encrypted bytes the player
/// downloaded are exactly what the key opens. Segments that fail to decrypt are skipped, not
/// reported as errors -- a partial file is an ordinary state for a cache being written to.
pub fn decrypt_into(
    cache: &Path,
    out: &Path,
    keys: &BTreeMap<u32, (String, String)>,
) -> Result<usize> {
    std::fs::create_dir_all(out)?;
    let mut written = 0usize;
    for (index, (file, key_text)) in keys {
        let Ok(ciphertext) = std::fs::read(cache.join(file)) else {
            continue;
        };
        let Ok(key) = key_from_text(key_text) else {
            continue;
        };
        let mask = mask_from_filename(file);
        let Ok(plain) = decrypt(&ciphertext, &key, &mask, file) else {
            continue;
        };
        std::fs::write(out.join(format!("{index:06}.ts")), &plain)?;
        written += 1;
    }
    Ok(written)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 376 not being a multiple of 16 has to stay nailed down: it once turned a whole round of
    /// key testing into a false negative, because the alignment error was swallowed.
    #[test]
    fn probe_bytes_is_block_aligned() {
        assert_eq!(PROBE_BYTES % 16, 0);
        assert_eq!(PROBE_BYTES % 188, 0);
        assert_ne!(188 * 2 % 16, 0);
    }
}
