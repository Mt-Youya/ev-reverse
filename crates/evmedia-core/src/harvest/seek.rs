//! Moving a playhead until a chosen segment falls inside the live window.
//!
//! The algorithm is portable and lives here so it can be tested without a player. Everything it
//! needs from one is two things — where the window is now, and how to step — which `evmedia-win`
//! supplies from the real player (a `WM_KEYDOWN`/`WM_KEYUP` post to its own window) and a test
//! supplies from a counter.
//!
//! Why it needs the playhead at all: a segment's key exists only once the player has decrypted it
//! for playback, so a gap left behind the playhead can only be filled by putting the playhead back
//! inside it and letting the read-ahead decrypt it again.
//!
//! The window doubles as the position readout. There is no other feedback available — the player's
//! rendering surface cannot be read — so each round measures how far a batch of steps moved the
//! window and sizes the next batch from that, rather than assuming a step size.

use anyhow::Result;
use evmedia_contract::Reporter;
use std::time::Duration;

/// What the seek needs from a player.
pub trait Playhead {
    /// The lowest and highest segment index whose key is live right now.
    ///
    /// `None` means the player is holding nothing, which is the caller's cue to stop.
    ///
    /// This must be the *keyed* set rather than every segment the player has a context for. The
    /// player keeps a context for each segment it has touched, and on a real lesson that is the
    /// whole lesson: a recorded dump from the research bench shows 134 contexts held with only 2
    /// decrypted. A window measured from those spans everything, so the "is the target already
    /// inside?" test below passes for a segment that was never decrypted and the seek posts no keys
    /// at all while reporting success.
    fn window(&self) -> Option<(u32, u32)>;

    /// Step the playhead `count` times, backwards when `back` is set.
    fn step(&self, back: bool, count: usize, gap: Duration);
}

/// Rounds to try before giving up on reaching the target.
const MAX_ROUNDS: usize = 8;
/// Bounds on one batch, so a bad measurement cannot fire thousands of keystrokes.
const MIN_PRESSES: f64 = 2.0;
const MAX_PRESSES: f64 = 400.0;
/// The estimate of segments-per-press never drops below this, which is what stops it collapsing to
/// zero after a round that moved nothing and then clamping every later batch to the maximum.
const MIN_PER_PRESS: f64 = 0.05;

/// Move the playhead until `target` sits inside the live window.
///
/// Generic over the playhead rather than taking `&dyn Playhead` so that a caller holding a
/// `&dyn Harvester` can pass it straight in: `Harvester` has `Playhead` as a supertrait, so the
/// object satisfies this bound without an upcast.
///
/// Returns false when the steps turn out to have no effect, so the caller can stop asking and
/// simply wait instead of hammering the player.
pub fn seek_window_to<P: Playhead + ?Sized>(
    playhead: &P,
    target: u32,
    press_gap: Duration,
    reporter: &Reporter,
) -> Result<bool> {
    // Segments moved per step. The first batch guesses 1 and the measurement corrects it.
    let mut per_press = 1.0f64;
    for round in 1..=MAX_ROUNDS {
        let Some((current, highest)) = playhead.window() else {
            reporter.info("  player is holding no segment window; nothing to aim at");
            return Ok(false);
        };
        if current <= target && target <= highest {
            return Ok(true);
        }
        let back = target < current;
        let distance = if back { (current - target) as f64 } else { (target - highest) as f64 };
        let presses = (distance / per_press).clamp(MIN_PRESSES, MAX_PRESSES) as usize;
        playhead.step(back, presses, press_gap);

        let Some((moved, moved_high)) = playhead.window() else {
            return Ok(false);
        };
        if moved == current && moved_high == highest {
            reporter.info(format!("  seek keys had no effect (window stayed {current}..{highest})"));
            return Ok(false);
        }
        let travelled = if back {
            (current as i64 - moved as i64).max(0) as f64
        } else {
            (moved_high as i64 - highest as i64).max(0) as f64
        };
        per_press = (travelled / presses as f64).max(MIN_PER_PRESS);
        reporter.info(format!(
            "  sweep {round}: {presses} x step-{} moved the window {current}..{highest} -> {moved}..{moved_high} (~{per_press:.2} seg/press)",
            if back { "back" } else { "forward" }
        ));
    }
    let covered = playhead
        .window()
        .is_some_and(|(low, high)| low <= target && target <= high);
    if !covered {
        reporter.info(format!("  sweep did not converge on index {target}"));
    }
    Ok(covered)
}
