//! The harvest loop and the seam it reads a player through.
//!
//! The loop itself is portable: it knows about segments, keys, URLs, gaps and the playhead,
//! but nothing about Windows. Everything platform-specific arrives through [`Harvester`],
//! which is also what lets `tests/grab_loop.rs` drive the real loop against a fixture.
//!
//! Why a gap needs the playhead at all: the AES schedule for a segment exists in the player
//! only once that segment has been decrypted for playback. A segment the playhead ran past was
//! never decrypted, so its key was never computed and no amount of waiting will produce it —
//! the playhead has to be walked back over the gap.

pub mod fetch;
pub mod grab;

use anyhow::Result;
use evmedia_contract::Reporter;
use std::{
    collections::{BTreeMap, HashMap},
    path::PathBuf,
    time::Duration,
};

/// One segment as the loop needs it: what to fetch, and what opens it.
pub struct Segment {
    pub index: u32,
    pub file: String,
    pub key: String,
    pub url: String,
}

/// What the loop needs from a live player.
pub trait Harvester {
    fn pid(&self) -> u32;

    /// Segment index -> (filename, key), for every segment whose key is live right now.
    fn keys(&self) -> Result<BTreeMap<u32, (String, String)>>;

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
