//! The live Windows source: a [`Player`] presented as a [`Harvester`].
//!
//! It is the [`Playhead`] as well, because the loop steers the player through this seam and reads
//! it through the same one.

use crate::{playhead, process::Player};
use anyhow::Result;
use evmedia_core::harvest::{seek::Playhead, Harvester};
use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    time::Duration,
};

pub struct WinSource {
    player: Player,
}

impl WinSource {
    pub fn open(pid: u32) -> Result<Self> {
        Ok(Self { player: Player::open(pid)? })
    }

    pub fn player(&self) -> &Player {
        &self.player
    }
}

impl Harvester for WinSource {
    fn pid(&self) -> u32 {
        self.player.pid()
    }

    fn keys(&self) -> BTreeMap<u32, (String, String)> {
        self.player.active_keys()
    }

    fn candidates(&self) -> BTreeSet<String> {
        self.player.hex_candidates()
    }

    fn indexes(&self) -> HashMap<String, u32> {
        self.player.segment_indexes()
    }

    fn urls(&self) -> HashMap<String, String> {
        self.player.segment_urls()
    }

    fn diagnose(&self) -> String {
        self.player.diagnose()
    }
}

impl Playhead for WinSource {
    /// The range of lesson indexes whose key is live right now.
    ///
    /// Read from the keys, not from every context the player holds. The player keeps a context for
    /// each segment it has touched, and on a real lesson that is the whole lesson — a recorded dump
    /// in the research bench shows 134 contexts held with only 2 decrypted. A window measured from
    /// those spans everything, so a seek is always told it has already arrived and never moves.
    ///
    /// A missing player window is not checked here. With nothing to post to, stepping changes
    /// nothing, and the seek reports that the steps had no effect — which is what happened.
    fn window(&self) -> Option<(u32, u32)> {
        let live = self.player.active_keys();
        Some((*live.keys().next()?, *live.keys().next_back()?))
    }

    fn step(&self, back: bool, count: usize, gap: Duration) {
        let Some(hwnd) = playhead::player_window(self.player.pid()) else {
            return;
        };
        let key = if back { playhead::STEP_BACK } else { playhead::STEP_FORWARD };
        playhead::post_key(hwnd, key, count, gap);
    }
}
