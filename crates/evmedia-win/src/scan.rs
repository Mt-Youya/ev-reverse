//! Finding things in the player's heap: the playback contexts that carry segment keys, and
//! the fully signed URLs it built for its own requests.

use crate::process::{Player, CONTEXT_SIZE, OFF_FILE, OFF_INDEX, OFF_MASK, OFF_SCHEDULE};
use evmedia_core::crypto::{is_hex32, schedule_to_key};
use evmedia_core::keyscan::{self, KeyEntry, Library};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::Path;

const URL_NEEDLE: &[u8] = b".ts?bid=";
const URL_MAX: usize = 1024;
/// The context objects' vtable lives in this range of this DLL, so a pointer into the range
/// marks a candidate object.
const RENDER_DLL: &str = "PlayerLibRender56_vs.dll";
const VTABLE_LOW: usize = 0x802000;
const VTABLE_HIGH: usize = 0x804000;

impl Player {
    /// One-shot accounting of what the scan can actually see, for troubleshooting.
    pub fn diagnose(&self) -> String {
        let mut regions = 0usize;
        let mut bytes_read = 0usize;
        let mut hex_runs = 0usize;
        let mut ts_hits = 0usize;
        self.for_each_chunk(4 << 20, |_, bytes| {
            regions += 1;
            bytes_read += bytes.len();
            for_each_hex32(bytes, |_| hex_runs += 1);
            let mut from = 0usize;
            while let Some(hit) = find(&bytes[from..], URL_NEEDLE) {
                ts_hits += 1;
                from += hit + 1;
            }
        });
        format!(
            "chunks={regions} bytes={} MiB hex32_runs={hex_runs} segment_url_hits={ts_hits}",
            bytes_read >> 20
        )
    }

    /// Segment index -> (filename, derived key) for every context whose key is already live.
    ///
    /// Probing every 8-byte step with ReadProcessMemory would be tens of millions of syscalls,
    /// so the vtable range check happens against the chunk already in hand and only a pointer
    /// that passes it costs a read.
    pub fn active_keys(&self) -> BTreeMap<u32, (String, String)> {
        let Some(base) = self.module_base(RENDER_DLL) else {
            return BTreeMap::new();
        };
        let (low, high) = (base + VTABLE_LOW, base + VTABLE_HIGH);
        let mut out = BTreeMap::new();
        self.for_each_chunk(4 << 20, |chunk_base, bytes| {
            for offset in (0..bytes.len().saturating_sub(8)).step_by(8) {
                let pointer =
                    u64::from_le_bytes(bytes[offset..offset + 8].try_into().unwrap()) as usize;
                if pointer < low || pointer >= high {
                    continue;
                }
                let Some(object) = self.read(chunk_base + offset, CONTEXT_SIZE) else { continue };
                let Some(mask) = self.string_field(&object, OFF_MASK) else { continue };
                if !is_hex32(&mask) {
                    continue;
                }
                let Some(file) = self.string_field(&object, OFF_FILE) else { continue };
                if !file.ends_with(".ts") {
                    continue;
                }
                let schedule: [u8; 32] = object[OFF_SCHEDULE..OFF_SCHEDULE + 32].try_into().unwrap();
                let Some(key) = schedule_to_key(&schedule) else { continue };
                let index = u32::from_le_bytes(object[OFF_INDEX..OFF_INDEX + 4].try_into().unwrap());
                out.insert(index, (file, key));
            }
        });
        out
    }

    /// Every 32-hex-character run in the process's readable memory.
    ///
    /// This is the shape a segment key has in memory: the player holds it as the 32 characters of
    /// its own hex text, used directly as the 32 bytes of an AES-256 key (see `crypto::key_from_text`).
    /// Reading candidates out is cheap and needs no structural assumption about where they live;
    /// deciding *which segment* each one opens is a separate step, done by trying them.
    pub fn hex_candidates(&self) -> BTreeSet<String> {
        let mut out = BTreeSet::new();
        self.for_each_chunk(4 << 20, |_, bytes| {
            for_each_hex32(bytes, |at| {
                if let Ok(text) = std::str::from_utf8(&bytes[at..at + 32]) {
                    out.insert(text.to_ascii_lowercase());
                }
            });
        });
        out
    }

    /// Segment filename -> playback index, read from the player's own playback context objects.
    ///
    /// The index cannot be parsed out of the API responses because the player does not keep them:
    /// a search of 759 MB of its heap found zero occurrences of `"idx":`. It lives here instead,
    /// one context per segment the player has touched, at a fixed offset.
    ///
    /// Note this is the *only* thing the context object is good for. Its key schedule slot
    /// (`OFF_SCHEDULE`) never holds the segment key — every key that opens a segment was found by
    /// testing hex candidates against the segment's bytes, not by reading this struct.
    pub fn segment_indexes(&self) -> HashMap<String, u32> {
        let Some(base) = self.module_base(RENDER_DLL) else {
            return HashMap::new();
        };
        let (low, high) = (base + VTABLE_LOW, base + VTABLE_HIGH);
        let mut out = HashMap::new();
        self.for_each_chunk(4 << 20, |chunk_base, bytes| {
            for offset in (0..bytes.len().saturating_sub(8)).step_by(8) {
                let pointer =
                    u64::from_le_bytes(bytes[offset..offset + 8].try_into().unwrap()) as usize;
                if pointer < low || pointer >= high {
                    continue;
                }
                let Some(object) = self.read(chunk_base + offset, CONTEXT_SIZE) else { continue };
                let Some(file) = self.string_field(&object, OFF_FILE) else { continue };
                if !file.ends_with(".ts") {
                    continue;
                }
                let index = u32::from_le_bytes(object[OFF_INDEX..OFF_INDEX + 4].try_into().unwrap());
                out.insert(file, index);
            }
        });
        out
    }

    /// Segment filename -> lesson id, from the signed URLs the player built for its own requests.
    ///
    /// Indexes restart at 0 for every lesson, so the index alone cannot say which lesson a segment
    /// belongs to; this is what disambiguates. The URL is the only place the player exposes it.
    ///
    /// Only URLs still resident in the heap are seen, so a segment the player has since forgotten
    /// comes back with no lesson and cannot be placed. That is why a library built before this
    /// existed has to be discarded rather than upgraded: its numbering was positional, not real.
    pub fn segment_lessons(&self) -> HashMap<String, String> {
        let mut out = HashMap::new();
        self.for_each_chunk(4 << 20, |_, bytes| {
            collect_lessons(bytes, &mut out);
        });
        out
    }

    /// Fill in the lesson and index of every library entry still missing them.
    ///
    /// A key alone is enough to decrypt but not enough to *place* a segment: without these, a
    /// multi-lesson library merges into one file of the wrong length, and does it without erroring.
    pub fn fill_metadata(&self, library: &mut Library) {
        let indexes = self.segment_indexes();
        let lessons = self.segment_lessons();
        for (file, entry) in library.iter_mut() {
            if let Some(index) = indexes.get(file) {
                entry.index.get_or_insert(*index);
            }
            if let Some(lesson) = lessons.get(file) {
                entry.lesson.get_or_insert_with(|| lesson.clone());
            }
        }
    }

    /// Filename -> key for every segment in `dir` that one of `candidates` opens.
    ///
    /// `skip` holds segments already solved and they are not re-tested. A sweep calls this every
    /// round with only the candidates that are new since the previous round, which is what keeps
    /// a long sweep affordable: the alternative is re-testing everything against everything.
    pub fn recover_pairs(
        &self,
        dir: &Path,
        candidates: &BTreeSet<String>,
        skip: &Library,
    ) -> BTreeMap<String, String> {
        keyscan::recover(dir, candidates, skip).unwrap_or_default()
    }

    /// One-shot scan, solve and place, for a caller that is not sweeping.
    pub fn recover_keys(&self, dir: &Path) -> Library {
        let candidates = self.hex_candidates();
        let mut library: Library = self
            .recover_pairs(dir, &candidates, &Library::new())
            .into_iter()
            .map(|(file, key)| (file, KeyEntry { key, index: None, lesson: None }))
            .collect();
        self.fill_metadata(&mut library);
        library
    }

    /// Segment filename -> the fully signed URL the player built for its own request.
    pub fn segment_urls(&self) -> HashMap<String, String> {
        let mut out = HashMap::new();
        self.for_each_chunk(4 << 20, |_, bytes| {
            let mut from = 0usize;
            while let Some(hit) = find(&bytes[from..], URL_NEEDLE) {
                let at = from + hit;
                from = at + 1;
                // The filename runs back to the previous '/'; +3 covers ".ts".
                let Some(slash) = bytes[..at].iter().rposition(|byte| *byte == b'/') else {
                    continue;
                };
                let name_start = slash + 1;
                let name_end = at + 3;
                if name_end > bytes.len() {
                    continue;
                }
                let Ok(name) = std::str::from_utf8(&bytes[name_start..name_end]) else {
                    continue;
                };
                if !looks_like_segment(name) {
                    continue;
                }
                // The URL starts at the nearest "http" before the name and runs to the first
                // byte that cannot appear in a query string.
                let search_from = name_start.saturating_sub(256);
                let Some(scheme) = find(&bytes[search_from..name_start], b"http://") else {
                    continue;
                };
                let url_start = search_from + scheme;
                let mut url_end = name_end;
                while url_end < bytes.len()
                    && is_url_byte(bytes[url_end])
                    && url_end - url_start < URL_MAX
                {
                    url_end += 1;
                }
                let Ok(url) = std::str::from_utf8(&bytes[url_start..url_end]) else {
                    continue;
                };
                if url.contains(".evplayer.cn/") && url.contains("sign=") {
                    out.entry(name.to_string()).or_insert_with(|| url.to_string());
                }
            }
        });
        out
    }
}

fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() || haystack.len() < needle.len() {
        return None;
    }
    haystack.windows(needle.len()).position(|window| window == needle)
}

/// Call `visit` with the start offset of every run of exactly 32 hex characters.
fn for_each_hex32<F: FnMut(usize)>(bytes: &[u8], mut visit: F) {
    let mut start: Option<usize> = None;
    for (index, byte) in bytes.iter().enumerate() {
        if byte.is_ascii_hexdigit() {
            if start.is_none() {
                start = Some(index);
            }
        } else {
            if let Some(from) = start {
                if index - from == 32 {
                    visit(from);
                }
            }
            start = None;
        }
    }
    if let Some(from) = start {
        if bytes.len() - from == 32 {
            visit(from);
        }
    }
}

fn is_url_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || b"-._~:/?#[]@!$&'()*+,;=%".contains(&byte)
}

/// The account id prefix plus a UUID, so the body must be hex and dashes only.
fn looks_like_segment(name: &str) -> bool {
    name.len() > 40
        && name.ends_with(".ts")
        && name[..name.len() - 3].bytes().all(|byte| byte.is_ascii_hexdigit() || byte == b'-')
}

/// Pull `<name>.ts?…&sid=<lesson>&…` pairs out of one chunk of memory.
fn collect_lessons(bytes: &[u8], out: &mut HashMap<String, String>) {
    let mut from = 0usize;
    while let Some(hit) = find(&bytes[from..], URL_NEEDLE) {
        // `hit` lands on the ".ts?bid=" of a signed URL; +3 covers the extension.
        let at = from + hit + 3;
        from = at;
        let Some(name) = name_before(bytes, at) else { continue };
        // Lossy, not strict: the URL is immediately followed by arbitrary heap bytes that are not
        // valid UTF-8, and a strict decode fails on them -- which silently discards every lesson
        // while still counting every needle hit. The `sid=` parameter is ASCII and always comes
        // before that trailing garbage, so replacing the garbage loses nothing.
        let tail = String::from_utf8_lossy(&bytes[at..(at + URL_MAX).min(bytes.len())]);
        if let Some(sid) = sid_of(&tail) {
            out.insert(name, sid);
        }
    }
}

/// The filename ending at `end`, or `None` when it does not look like a segment name.
fn name_before(bytes: &[u8], end: usize) -> Option<String> {
    let search_from = end.saturating_sub(128);
    let slash = bytes[search_from..end].iter().rposition(|byte| *byte == b'/')?;
    let name = std::str::from_utf8(&bytes[search_from + slash + 1..end]).ok()?;
    looks_like_segment(name).then(|| name.to_string())
}

/// The `sid` query parameter — the lesson a segment belongs to.
///
/// These bytes come straight out of the heap, so they are raw text and `&` may appear escaped as
/// `&`; looking for `sid=` finds the parameter either way.
fn sid_of(text: &str) -> Option<String> {
    let at = text.find("sid=")? + 4;
    let digits: String = text[at..].chars().take_while(char::is_ascii_digit).collect();
    (!digits.is_empty()).then_some(digits)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The exact shape the player leaves on its heap, wrapper and all.
    const SIGNED: &[u8] = b"\x00\x00http://cn28027.evplayer.cn/a5806965-1269-44ff-8cf3/\
119354-333c99e0-4045-4224-8c40-fb511d8450d6.ts?bid=119354&sid=1113723&t=6aa6e14c&v=2.0&sign=625e1efe\x00";

    #[test]
    fn a_signed_url_yields_its_segment_and_lesson() {
        let mut out = HashMap::new();
        collect_lessons(SIGNED, &mut out);
        assert_eq!(
            out.get("119354-333c99e0-4045-4224-8c40-fb511d8450d6.ts").map(String::as_str),
            Some("1113723"),
            "heap bytes were {:?}",
            String::from_utf8_lossy(SIGNED)
        );
    }

    #[test]
    fn a_lesson_id_stops_at_the_next_separator() {
        assert_eq!(sid_of("?bid=1&sid=1113723&t=x").as_deref(), Some("1113723"));
        assert_eq!(sid_of("?sid=42").as_deref(), Some("42"));
        assert_eq!(sid_of("?bid=1").as_deref(), None);
    }

    /// The heap puts the URL hard against arbitrary bytes. A strict `from_utf8` over that tail
    /// fails, and failing there drops every lesson while still counting every needle hit — a
    /// silent, one-sided failure that looks like "this player exposes no lesson ids".
    #[test]
    fn a_url_followed_by_binary_bytes_still_yields_its_lesson() {
        let mut raw = SIGNED.to_vec();
        raw.extend_from_slice(&[0xab, 0xab, 0xf0, 0xad, 0xba, 0x00, 0x80]);
        let mut out = HashMap::new();
        collect_lessons(&raw, &mut out);
        assert_eq!(
            out.get("119354-333c99e0-4045-4224-8c40-fb511d8450d6.ts").map(String::as_str),
            Some("1113723")
        );
    }
}
