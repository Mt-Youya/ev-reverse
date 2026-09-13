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
//! Two sources are read, and the order they are read in is what makes a round cheap. The live
//! playback contexts answer first, because one read per context yields the index, the filename and
//! the key together: nothing is searched for and nothing is tested. The heap-wide candidate scan
//! answers second, and only when the first found nothing — it is the only way to reach a key whose
//! context the player has already released, and paying for it every round would mean walking the
//! whole heap and running AES trials to learn what the previous line already knew.
//!
//! Nothing here assumes a step size. Each round simply presses the key a fixed number of times and
//! rescans; a press that moves the playhead further than the read-ahead window would leave a hole,
//! and holes are what the idle counter detects.

use crate::playhead;
use crate::process::Player;
use anyhow::Result;
use evmedia_contract::Reporter;
use evmedia_core::keyscan::{KeyEntry, Library};
use std::collections::btree_map::Entry;
use std::collections::BTreeSet;
use std::path::Path;
use std::time::{Duration, Instant};

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

/// Absorb every key the live contexts carry, returning how many files were new.
///
/// The cheap source: an entry arrives fully placed, so there is nothing to search for and nothing
/// to test. A file already in the library is not replaced — its key cannot have changed — but an
/// index it was missing is filled in, which is what a library built by the candidate path needs
/// before it can be merged.
fn absorb_live(player: &Player, library: &mut Library) -> usize {
    let mut gained = 0usize;
    for (index, (file, key)) in player.active_keys() {
        match library.entry(file) {
            Entry::Vacant(slot) => {
                slot.insert(KeyEntry { key, index: Some(index), lesson: None });
                gained += 1;
            }
            Entry::Occupied(mut slot) => {
                let entry = slot.get_mut();
                if entry.index.is_none() {
                    entry.index = Some(index);
                }
            }
        }
    }
    gained
}

/// Absorb whatever the heap still holds for segments whose context is gone.
///
/// Only candidates that have not been tried before are tested: a key already tried against a
/// segment cannot start opening it later, so re-testing is pure cost — and it is the difference
/// between a few hundred thousand AES calls per round and tens of millions.
fn absorb_candidates(
    player: &Player,
    cache: &Path,
    library: &mut Library,
    tested: &mut BTreeSet<String>,
) -> usize {
    let fresh: BTreeSet<String> = player.hex_candidates().difference(tested).cloned().collect();
    tested.extend(fresh.iter().cloned());
    let before = library.len();
    for (file, key) in player.recover_pairs(cache, &fresh, library) {
        library.entry(file).or_insert(KeyEntry { key, index: None, lesson: None });
    }
    library.len() - before
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
        let round_started = Instant::now();
        let sent = playhead::press(player, playhead::STEP_FORWARD, options.batch, options.gap);
        if sent == 0 {
            reporter.info("  no visible player window; cannot sweep".to_string());
            break;
        }

        let mut gained = absorb_live(player, library);

        // The second chance, taken only when the first came up empty. A key outlives the context
        // that carried it, and once the context is gone the key is a loose 32-hex string that the
        // read above cannot see. This is expensive, so it is not paid every round — but it is
        // paid before a round is called idle, because that is when missing it would cost a key.
        if gained == 0 {
            gained = absorb_candidates(player, cache, library, &mut tested);
        }

        // Placement is filled in per round, not at the end, so a sweep that is interrupted still
        // leaves behind a library that can be merged rather than only decrypted. It is a heap
        // walk of its own, so it follows the keys rather than running regardless.
        if gained > 0 {
            player.fill_metadata(library);
        }

        reporter.info(format!(
            "  round {round}: {sent} press(es), {gained} new key(s), {} total, {}ms",
            library.len(),
            round_started.elapsed().as_millis()
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

    // One last pass so entries that arrived with an index but no lesson are placed before the
    // caller groups and merges them.
    player.fill_metadata(library);
    Ok(library.len() - started_with)
}
