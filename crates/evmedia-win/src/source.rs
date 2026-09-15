//! The live Windows source: a [`Player`] presented as a [`Harvester`].
//!
//! It is the [`Playhead`] as well, because the loop steers the player through this seam and reads
//! it through the same one.

use crate::{playhead, process::Player};
use anyhow::{bail, Result};
use evmedia_core::harvest::{seek::Playhead, Harvester};
use evmedia_core::keyscan::lesson_from_url;
use std::{
    cell::RefCell,
    collections::{BTreeMap, BTreeSet, HashMap},
    time::Duration,
};

pub struct WinSource {
    player: Player,
    lesson: Option<String>,
    files: RefCell<BTreeSet<String>>,
}

impl WinSource {
    pub fn open(pid: u32) -> Result<Self> {
        Ok(Self { player: Player::open(pid)?, lesson: None, files: RefCell::default() })
    }

    pub fn open_lesson(pid: u32, requested: Option<String>, saved_files: BTreeSet<String>) -> Result<Self> {
        let mut source = Self::open(pid)?;
        source.files.borrow_mut().extend(saved_files);
        let urls = source.player.segment_urls();
        let mut counts = BTreeMap::<String, usize>::new();
        for lesson in urls.values().filter_map(|url| lesson_from_url(url)) {
            *counts.entry(lesson).or_default() += 1;
        }
        let lesson = match requested {
            Some(lesson) => lesson,
            None if counts.len() == 1 => counts.keys().next().unwrap().clone(),
            None => bail!("select a lesson with --lesson <UUID>; visible lesson directories (segment URLs): {:?}", counts),
        };
        source.files.borrow_mut().extend(urls.into_iter().filter_map(|(file, url)| {
            (lesson_from_url(&url).as_ref() == Some(&lesson)).then_some(file)
        }));
        source.lesson = Some(lesson);
        Ok(source)
    }

    pub fn lesson(&self) -> Option<&str> { self.lesson.as_deref() }

    pub fn player(&self) -> &Player {
        &self.player
    }
}

impl Harvester for WinSource {
    fn pid(&self) -> u32 {
        self.player.pid()
    }

    fn keys(&self) -> BTreeMap<u32, (String, String)> {
        if self.lesson.is_some() {
            self.player.active_keys_for(Some(&self.files.borrow()))
        } else { self.player.active_keys() }
    }

    fn candidates(&self) -> BTreeSet<String> {
        self.player.hex_candidates()
    }

    fn indexes(&self) -> HashMap<String, u32> {
        let mut indexes = self.player.segment_indexes();
        if self.lesson.is_some() { indexes.retain(|file, _| self.files.borrow().contains(file)); }
        indexes
    }

    fn urls(&self) -> HashMap<String, String> {
        let mut urls = self.player.segment_urls();
        if let Some(lesson) = &self.lesson {
            urls.retain(|_, url| lesson_from_url(url).as_ref() == Some(lesson));
            self.files.borrow_mut().extend(urls.keys().cloned());
        }
        urls
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
        let live = self.keys();
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
