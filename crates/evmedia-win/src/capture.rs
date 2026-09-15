//! `capture-ev`: write a manifest for a lesson from a live player.
//!
//! Keys come from two sources, the same two the harvest loop uses. A live playback context gives
//! the index, the filename and the key together; a key whose context the player has released is
//! only a loose 32-hex string on the heap, and has to be tested against the segment's own bytes
//! to be attributed to it. Neither source covers everything, so both are consulted.
//!
//! This module is only the part that needs a player. Assembling the manifest — checking that
//! every segment has a key and that the indexes are contiguous — is portable and lives in
//! `decode::build_manifest`, where it is tested without one.
//!
//! The command also replaces an earlier collector whose only key source was a salt-based
//! derivation that this build turned out not to implement — it searched process memory for
//! `tk + filename` and never found it, so that command could only ever fail. See
//! `docs/ARCHITECTURE.md`.
//!
//! PRECONDITION: every segment in `input` must have been decrypted by the player at least once,
//! which means the lesson has been played through. This is a consequence of where keys come
//! from, not a limitation that can be coded around here.

use crate::process::Player;
use anyhow::{bail, Context, Result};
use evmedia_contract::Reporter;
use evmedia_core::{
    decode,
    keyscan::{self, Library},
};
use std::{collections::HashMap, path::Path};

const TOOL: &str = "EVPlayer2 5.0.5 Rust live collector";

pub fn capture(pid: u32, input: &Path, output: &Path, reporter: &Reporter) -> Result<()> {
    let player = Player::open(pid)?;

    // Filename -> (index, key). The live contexts fill this first because they are exact: one
    // read per context, with the index and the key arriving together.
    let mut entries: HashMap<String, (u32, String)> = player
        .active_keys()
        .into_iter()
        .map(|(index, (file, key))| (file, (index, key)))
        .collect();

    // Then the candidate search, for whatever they could not reach. The index is not in the
    // segment and not in the filename, so a key recovered this way still needs the player to say
    // where the segment sits — and if the player has forgotten, the key is unusable here.
    let indexes = player.segment_indexes();
    let candidates = player.hex_candidates();
    for (file, key) in keyscan::recover(input, &candidates, &Library::new())? {
        if let Some(index) = indexes.get(&file) {
            entries.entry(file).or_insert((*index, key));
        }
    }

    let manifest = decode::build_manifest(input, &entries, TOOL)?;

    if output.exists() {
        bail!("refusing to overwrite {}", output.display());
    }
    if let Some(parent) = output.parent() {
        std::fs::create_dir_all(parent)?;
    }
    serde_json::to_writer_pretty(
        std::fs::File::create(output).with_context(|| format!("create {}", output.display()))?,
        &manifest,
    )?;
    reporter.info(format!(
        "Created {} ({} segment(s))",
        output.display(),
        manifest.segment_count
    ));
    Ok(())
}
