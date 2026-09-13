//! The `EvManifest` JSON contract: a verified, ordered set of encrypted segments plus the key
//! material needed to turn each one into MPEG-TS.

use crate::crypto::{
    decrypt, hex_lower, key_from_hex, mask_from_filename, mask_from_hex, sha256_hex,
};
use anyhow::{anyhow, bail, Result};
use evmedia_contract::Reporter;
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    io::{Read, Write},
    path::Path,
};

#[derive(Debug, Deserialize)]
pub struct EvManifest {
    #[serde(default)]
    pub tool: String,
    #[serde(default)]
    pub variant: String,
    pub segments: Vec<EvSegment>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct EvSegment {
    pub index: u32,
    pub file: String,
    pub key_hex: String,
    pub xor_mask_hex: String,
    pub encrypted_sha256: String,
}

/// What `capture-ev` writes. Deliberately the same three top-level fields the pre-workspace
/// collector emitted — `decode-ev` accepts it because its `EvManifest` treats `variant` as
/// optional and ignores unknown fields.
#[derive(Debug, Serialize)]
pub struct CaptureManifest {
    pub tool: String,
    pub segment_count: usize,
    pub segments: Vec<EvSegment>,
}

/// Assemble a capture manifest from a live player's key material.
///
/// `keys` maps segment filename -> (playback index, key text), as the player reported them. Two
/// things make this fail rather than emit a manifest that looks fine and is not:
///
/// * a segment in `input` with no entry in `keys` — the player has not decrypted it, so no key
///   for it exists anywhere and the manifest would silently omit it;
/// * indexes that are not a contiguous `0..n` — `decode-ev` reassembles by position, so a gap
///   would produce a file of the wrong length, and nothing downstream would notice.
///
/// This is the portable half of `capture-ev`. Everything here works from files on disk and a map,
/// which is what makes the command testable without a player.
pub fn build_manifest(
    input: &Path,
    keys: &HashMap<String, (u32, String)>,
    tool: &str,
) -> Result<CaptureManifest> {
    let names: Vec<String> = list_input_names(input)?
        .into_iter()
        .filter(|name| name.ends_with(".ts"))
        .collect();
    if names.is_empty() {
        bail!("input holds no .ts segments");
    }

    let mut found: Vec<(u32, String, String)> = Vec::new();
    let mut missing: Vec<String> = Vec::new();
    for name in names {
        match keys.get(&name) {
            Some((index, key)) => found.push((*index, name, key.clone())),
            None => missing.push(name),
        }
    }
    if !missing.is_empty() {
        bail!(
            "no key for {} of {} segment(s); the player derives a key only while it plays a \
             segment, so play the lesson through and retry. First missing: {}",
            missing.len(),
            missing.len() + found.len(),
            missing[0]
        );
    }

    found.sort_by_key(|(index, _, _)| *index);
    if found.iter().enumerate().any(|(position, (index, _, _))| *index != position as u32) {
        bail!("segment indexes are not a contiguous 0..n range; the lesson is not fully decrypted");
    }

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
    Ok(CaptureManifest { tool: tool.to_string(), segment_count: segments.len(), segments })
}

/// Read one segment's encrypted bytes out of a directory or a ZIP.
pub fn find_input_bytes(input: &Path, name: &str) -> Result<Vec<u8>> {
    if input.is_dir() {
        return std::fs::read(input.join(name)).map_err(|error| anyhow!("read segment {name}: {error}"));
    }
    let file = std::fs::File::open(input)?;
    let mut archive = zip::ZipArchive::new(file)?;
    let mut found = None;
    for index in 0..archive.len() {
        let mut member = archive.by_index(index)?;
        if Path::new(member.name()).file_name().and_then(|value| value.to_str()) != Some(name) {
            continue;
        }
        if found.is_some() {
            bail!("duplicate ZIP member filename: {name}");
        }
        let mut bytes = Vec::new();
        member.read_to_end(&mut bytes)?;
        found = Some(bytes);
    }
    found.ok_or_else(|| anyhow!("missing ZIP member {name}"))
}

pub fn decode_segment(data: &[u8], item: &EvSegment) -> Result<Vec<u8>> {
    if data.is_empty() || data.len() % 16 != 0 {
        bail!("{} is incomplete", item.file);
    }
    if sha256_hex(data) != item.encrypted_sha256.to_lowercase() {
        bail!("{} fingerprint mismatch", item.file);
    }
    let key = key_from_hex(&item.key_hex).map_err(|_| anyhow!("{} has invalid key material", item.file))?;
    let mask =
        mask_from_hex(&item.xor_mask_hex).map_err(|_| anyhow!("{} has invalid key material", item.file))?;
    let buffer = decrypt(data, &key, &mask, &item.file)?;
    // Stricter than the live path, and on purpose: a manifest came with a recorded fingerprint,
    // so a bad adaptation-field control is a real corruption signal here. Moving this into
    // `crypto::decrypt` would start rejecting segments the live path accepts today.
    if (0..buffer.len()).step_by(188).any(|index| ((buffer[index + 3] >> 4) & 3) == 0) {
        bail!("{} has an invalid TS packet", item.file);
    }
    Ok(buffer)
}

/// Names of the segment members in a directory or ZIP, without reading their contents.
pub fn list_input_names(input: &Path) -> Result<Vec<String>> {
    if input.is_dir() {
        let mut names = Vec::new();
        for entry in std::fs::read_dir(input)? {
            let entry = entry?;
            if entry.file_type()?.is_file() {
                names.push(entry.file_name().to_string_lossy().to_string());
            }
        }
        return Ok(names);
    }
    let mut archive = zip::ZipArchive::new(std::fs::File::open(input)?)?;
    let mut names = Vec::new();
    for index in 0..archive.len() {
        let member = archive.by_index(index)?;
        if member.is_dir() {
            continue;
        }
        names.push(
            Path::new(member.name())
                .file_name()
                .and_then(|value| value.to_str())
                .ok_or_else(|| anyhow!("invalid ZIP filename"))?
                .to_string(),
        );
    }
    Ok(names)
}

pub fn decode_ev(input: &Path, manifest: EvManifest, output: &Path, reporter: &Reporter) -> Result<()> {
    if !manifest.tool.contains("EVPlayer2 5.0.5") && manifest.variant != "xor16_then_aes256ecb_hash_padding" {
        bail!("manifest is not from a supported EVPlayer2 5.0.5 collector");
    }
    let mut items = manifest.segments;
    items.sort_by_key(|item| item.index);
    if items.is_empty() || items.iter().enumerate().any(|(index, item)| item.index != index as u32) {
        bail!("manifest indexes are incomplete");
    }
    if output.exists() {
        bail!("output already exists: {}", output.display());
    }
    if let Some(parent) = output.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let partial = output.with_extension("partial");
    let mut destination = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&partial)?;
    for item in &items {
        destination.write_all(&decode_segment(&find_input_bytes(input, &item.file)?, item)?)?;
    }
    destination.flush()?;
    std::fs::rename(partial, output)?;
    reporter.info(format!(
        "Decoded {} verified segments to {}",
        items.len(),
        output.display()
    ));
    Ok(())
}
