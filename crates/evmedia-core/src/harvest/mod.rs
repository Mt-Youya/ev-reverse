//! The harvest loop and the seam it reads a player through.
//!
//! The loop itself is portable: it knows about segments, keys, URLs, gaps and the playhead,
//! but nothing about Windows. Everything platform-specific arrives through [`Harvester`],
//! which is also what lets `tests/grab_loop.rs` drive the real loop against a fixture.
//!
//! Why a gap needs the playhead at all: a segment's key exists in the player only once that
//! segment has been decrypted for playback. A segment the playhead ran past was never decrypted,
//! so its key was never computed and no amount of waiting will produce it — the playhead has to
//! be walked back over the gap.

pub mod fetch;
pub mod grab;
pub mod seek;

use anyhow::Result;
use evmedia_contract::Reporter;
use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    path::PathBuf,
    time::Duration,
};

/// One segment as the loop needs it: what to fetch, and what opens it.
pub struct Segment {
    pub index: u32,
    pub file: String,
    pub key: String,
    /// The signed URL to fetch the ciphertext from, when the player is still offering one.
    ///
    /// Optional on purpose. The player drops signed URLs as playback moves past them, but a key,
    /// an index and a cached ciphertext are all permanent — so a segment whose bytes are already
    /// on disk needs no URL at all, and requiring one would strand it.
    pub url: Option<String>,
}

/// What the loop needs from a live player.
pub trait Harvester {
    fn pid(&self) -> u32;

    /// Segment index -> (filename, key) for every segment whose key is live right now.
    ///
    /// The cheapest source and the most precise: the index and the filename arrive with the key,
    /// so a segment learned this way needs nothing worked out about it. It is not a replacement
    /// for [`Harvester::candidates`] — a key outlives the context that carried it, and a key whose
    /// context is gone is just a loose 32-hex string that has to be tested against segment bytes
    /// to be attributed.
    fn keys(&self) -> BTreeMap<u32, (String, String)>;

    /// Every 32-hex-character run in the player's readable memory, right now.
    ///
    /// The loop tests these against the ciphertext on disk itself, through `keyscan::recover`,
    /// which is what recovers the keys whose context the player has already released — the ones
    /// [`Harvester::keys`] can no longer see. Cheap it is not: this walks the whole heap.
    fn candidates(&self) -> BTreeSet<String>;

    /// Segment filename -> the playback index the player is holding for it, right now.
    ///
    /// The index is the one thing a key cannot tell you: it is not in the filename and not in the
    /// segment, so it can only come from the player. The loop keeps the latest answer it has seen
    /// for each file, because the player releases contexts as playback moves on and an index that
    /// has been read once must outlive the object it was read from. (The mapping is stable — a
    /// filename belongs to one index for good — so latest and highest are the same answer; the
    /// loop just takes whichever arrives.)
    fn indexes(&self) -> HashMap<String, u32>;

    /// Segment filename -> the fully signed URL the player built for its own request.
    fn urls(&self) -> HashMap<String, String>;

    /// One-shot description of what a scan can actually see, for troubleshooting.
    fn diagnose(&self) -> String;

    /// Move the playhead so `target` falls inside the live key window. `Ok(false)` means this
    /// source cannot steer a playhead — the loop then stops asking and simply waits.
    fn seek_to(&self, target: u32, press_gap: Duration, reporter: &Reporter) -> Result<bool> {
        let _ = (target, press_gap, reporter);
        Ok(false)
    }
}

/// Polls with no newly decrypted segment before the loop decides playback has stalled.
pub const SWEEP_AFTER: usize = 5;
/// Upper bound on sweep attempts, so an unbound or ineffective key cannot loop forever.
pub const MAX_SWEEPS: usize = 20;

pub struct GrabOptions {
    pub output: PathBuf,
    pub jobs: usize,
    pub poll: Duration,
    pub idle_limit: usize,
    pub attempts: usize,
    pub mp4: bool,
    /// Steer the playhead back over gaps the player skipped.
    pub sweep: bool,
    /// Delay between posted seek keystrokes.
    pub press_gap: Duration,
}

impl Default for GrabOptions {
    fn default() -> Self {
        Self {
            output: PathBuf::from(evmedia_contract::args::DEFAULT_OUTPUT),
            jobs: evmedia_contract::args::DEFAULT_JOBS,
            poll: Duration::from_secs(evmedia_contract::args::DEFAULT_POLL_SECS),
            idle_limit: evmedia_contract::args::DEFAULT_IDLE_LIMIT,
            attempts: evmedia_contract::args::DEFAULT_ATTEMPTS,
            mp4: false,
            sweep: true,
            press_gap: Duration::from_millis(evmedia_contract::args::DEFAULT_SWEEP_GAP_MS),
        }
    }
}
