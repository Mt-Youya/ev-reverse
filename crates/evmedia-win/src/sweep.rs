//! Walking the playhead across a whole lesson so the player decrypts every segment on the way.
//!
//! This is the only way to get a complete key set. The player computes a segment's key at the
//! moment it decrypts that segment for playback and at no other time — downloading a segment
//! produces nothing, which was verified directly: with the player downloading and not playing,
//! a scan of its memory found no key at all. So the playhead has to actually pass over every
//! segment.
//!
//! What makes that affordable is that a key, once computed, is never dropped. Sampling four times
//! twenty seconds apart during real playback showed keys only ever accumulating (0 vanished each
//! round) at roughly 27 segments per 20 seconds. Pressing the step-forward key is about nine times
//! faster than that — 60 presses moved 73 segments in 6 seconds, i.e. ~12/s against ~1.35/s — so
//! a lesson that would take 75 minutes to watch is swept in well under 20.
//!
//! Nothing here assumes a step size. Each round simply presses the key a fixed number of times and
//! rescans; a press that moves the playhead further than the read-ahead window would leave a hole,
//! and holes are what the idle counter detects.

use crate::playhead;
use crate::process::Player;
use anyhow::Result;
use evmedia_contract::Reporter;
use evmedia_core::keyscan::{KeyEntry, Library};
use std::collections::BTreeSet;
use std::path::Path;
use std::time::Duration;

pub struct SweepOptions {
    /// Step-forward presses posted per round.
    pub batch: usize,
    /// Delay between presses. Shorter risks outrunning the player's decryption.
    pub gap: Duration,
    /// Stop after this many consecutive rounds with nothing new.
    pub idle_rounds: usize,
    /// Hard round cap, so a player that stops responding cannot loop forever.
    pub max_rounds: usize,
}

impl Default for SweepOptions {
    fn default() -> Self {
        Self { batch: 60, gap: Duration::from_millis(100), idle_rounds: 3, max_rounds: 400 }
    }
}

/// Walk the playhead across the lesson, merging every key found along the way into `library`.
///
/// Returns how many keys this sweep added. `library` is both the skip set and the output, so a
/// sweep resumed later continues where it stopped instead of re-testing solved segments.
pub fn collect(
    player: &Player,
    cache: &Path,
    library: &mut Library,
    options: &SweepOptions,
    reporter: &Reporter,
) -> Result<usize> {
    let started_with = library.len();
    let mut tested: BTreeSet<String> = BTreeSet::new();
    let mut idle = 0usize;

    for round in 1..=options.max_rounds {
        let sent = playhead::press(player, playhead::STEP_FORWARD, options.batch, options.gap);
        if sent == 0 {
            reporter.info("  no visible player window; cannot sweep".to_string());
            break;
        }

        // Only candidates that appeared since the previous round are worth testing. A key already
        // tried against a segment cannot start opening it later, so re-testing is pure cost — and
        // it is the difference between a few hundred thousand AES calls per round and tens of
        // millions.
        let fresh: BTreeSet<String> =
            player.hex_candidates().difference(&tested).cloned().collect();
        tested.extend(fresh.iter().cloned());

        let found = player.recover_pairs(cache, &fresh, library);
        let before = library.len();
        for (file, key) in found {
            library.entry(file).or_insert(KeyEntry { key, index: None, lesson: None });
        }
        let gained = library.len() - before;

        // Placement is filled in per round, not at the end, so a sweep that is interrupted still
        // leaves behind a library that can be merged rather than only decrypted.
        player.fill_metadata(library);

        reporter.info(format!(
            "  round {round}: {sent} press(es), {gained} new key(s), {} total",
            library.len()
        ));

        if gained == 0 {
            idle += 1;
            if idle >= options.idle_rounds {
                reporter.info(format!(
                    "  nothing new for {} round(s); stopping",
                    options.idle_rounds
                ));
                break;
            }
        } else {
            idle = 0;
        }
    }

    Ok(library.len() - started_with)
}
