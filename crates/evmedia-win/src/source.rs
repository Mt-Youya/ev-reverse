//! The live Windows source: a [`Player`] presented as a [`Harvester`].

use crate::{playhead, process::Player};
use anyhow::Result;
use evmedia_contract::Reporter;
use evmedia_core::harvest::Harvester;
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

    fn seek_to(&self, target: u32, press_gap: Duration, reporter: &Reporter) -> Result<bool> {
        playhead::seek_window_to(&self.player, target, press_gap, reporter)
    }
}
