//! `capture-ev`: write a manifest for a lesson from a live player.
//!
//! Keys come from the same schedule the harvest loop uses, so this path is live. It replaces an
//! earlier collector whose only key source was a salt-based derivation that this build turned
//! out not to implement — it searched process memory for `tk + filename` and never found it, so
//! that command could only ever fail. See `docs/ARCHITECTURE.md`.
//!
//! PRECONDITION: every segment in `input` must already be decrypted by the player, which means
//! the lesson has been played through. This is a consequence of where keys come from, not a
//! limitation that can be coded around here.
//!
//! Re-verify this path against a real lesson before relying on it: it is the one command whose
//! behaviour changed rather than moved.

use crate::process::Player;
use anyhow::{bail, Context, Result};
use evmedia_contract::Reporter;
use evmedia_core::{
    crypto::{hex_lower, mask_from_filename, sha256_hex},
    decode::{find_input_bytes, list_input_names, CaptureManifest, EvSegment},
};
use std::path::Path;

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
    let keys = player.active_keys();
    // index -> (file, key) inverted to file -> (index, key), because the input is a list of files.
    let by_file: std::collections::HashMap<&str, (u32, &str)> = keys
        .iter()
        .map(|(index, (file, key))| (file.as_str(), (*index, key.as_str())))
        .collect();

    let mut missing = Vec::new();
    let mut found: Vec<(u32, String, String)> = Vec::new();
    for name in &names {
        match by_file.get(name.as_str()) {
            Some((index, key)) => found.push((*index, name.clone(), (*key).to_string())),
            None => missing.push(name.clone()),
        }
    }
    if !missing.is_empty() {
        bail!(
            "the player has not decrypted {} of {} segment(s) yet; play the lesson through and retry",
            missing.len(),
            names.len()
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
