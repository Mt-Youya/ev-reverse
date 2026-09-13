//! `capture-ev`: write a manifest for a lesson from a live player.
//!
//! Keys come from two sources, the same two the harvest loop uses. A live playback context gives
//! the index, the filename and the key together; a key whose context the player has released is
//! only a loose 32-hex string on the heap, and has to be tested against the segment's own bytes
//! to be attributed to it. Neither source covers everything, so both are consulted.
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
    crypto::{hex_lower, mask_from_filename, sha256_hex},
    decode::{find_input_bytes, list_input_names, CaptureManifest, EvSegment},
    keyscan::{self, Library},
};
use std::{collections::HashMap, path::Path};

const TOOL: &str = "EVPlayer2 5.0.5 Rust live collector";

pub fn capture(pid: u32, input: &Path, output: &Path, reporter: &Reporter) -> Result<()> {
    let names: Vec<String> = list_input_names(input)?
        .into_iter()
        .filter(|name| name.ends_with(".ts"))
        .collect();
    if names.is_empty() {
        bail!("input holds no .ts segments");
    }

    let player = Player::open(pid)?;
    // Filename -> (index, key). The live contexts fill this first because they are exact; the
    // candidate search then adds whatever they could not reach.
    let mut entries: HashMap<String, (u32, String)> = player
        .active_keys()
        .into_iter()
        .map(|(index, (file, key))| (file, (index, key)))
        .collect();
    let indexes = player.segment_indexes();
    let candidates = player.hex_candidates();
    for (file, key) in keyscan::recover(input, &candidates, &Library::new())? {
        if let Some(index) = indexes.get(&file) {
            // The index is not in the segment and not in the filename, so a key recovered this
            // way still needs the player to say where the segment sits.
            entries.entry(file).or_insert((*index, key));
        }
    }

    let mut missing = Vec::new();
    let mut found: Vec<(u32, String, String)> = Vec::new();
    for name in &names {
        match entries.get(name) {
            Some((index, key)) => found.push((*index, name.clone(), key.clone())),
            None => missing.push(name.clone()),
        }
    }
    if !missing.is_empty() {
        bail!(
            "no key and index for {} of {} segment(s); the player derives a key only while it \
             plays a segment, so play the lesson through and retry. First missing: {}",
            missing.len(),
            names.len(),
            missing[0]
        );
    }
    found.sort_by_key(|(index, _, _)| *index);

    let mut segments = Vec::with_capacity(found.len());
    for (index, file, key) in found {
        let bytes = find_input_bytes(input, &file)?;
        segments.push(EvSegment {
            index,
            key_hex: hex_lower(key.as_bytes()),
            xor_mask_hex: hex_lower(&mask_from_filename(&file)),
            encrypted_sha256: sha256_hex(&bytes),
            file,
        });
    }
    // `decode-ev` insists on exactly 0..n-1, so fail here with a clearer message than it would.
    if segments.iter().enumerate().any(|(position, item)| item.index != position as u32) {
        bail!("segment indexes are not a contiguous 0..n range; the lesson is not fully decrypted");
    }

    if output.exists() {
        bail!("refusing to overwrite {}", output.display());
    }
    if let Some(parent) = output.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let manifest = CaptureManifest { tool: TOOL.to_string(), segment_count: segments.len(), segments };
    serde_json::to_writer_pretty(
        std::fs::File::create(output).with_context(|| format!("create {}", output.display()))?,
        &manifest,
    )?;
    reporter.info(format!("Created {}", output.display()));
    Ok(())
}
