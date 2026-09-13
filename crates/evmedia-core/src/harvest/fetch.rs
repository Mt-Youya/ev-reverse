//! Fetching one segment and turning it into a decrypted file on disk.
//!
//! Ciphertext is cached under `enc/` and plaintext under `dec/`, both keyed by segment index,
//! so an interrupted run resumes rather than re-downloading. Writes land on a temporary name
//! and are renamed into place, because the merge step treats any file it finds as complete.

use super::Segment;
use crate::crypto::{decrypt, key_from_text, mask_from_filename};
use anyhow::{bail, Context, Result};
use std::{collections::BTreeMap, fs, path::Path};

/// Smallest plausible segment: two 188-byte packets.
const MIN_SEGMENT: u64 = 376;

/// True if `dec/<index>.ts` already holds a plausible decrypted segment.
pub fn valid_dec_file(dir: &Path, index: u32) -> bool {
    let path = dir.join(format!("{index:06}.ts"));
    match fs::metadata(&path) {
        Ok(meta) if meta.len() >= MIN_SEGMENT && meta.len() % 188 == 0 => {
            fs::read(&path).map(|bytes| bytes[0] == 0x47 && bytes[188] == 0x47).unwrap_or(false)
        }
        _ => false,
    }
}

/// Everything already decrypted under `dec/`, so a rerun picks up where the last one stopped.
pub fn resume(dec_dir: &Path) -> BTreeMap<u32, String> {
    let mut found = BTreeMap::new();
    if let Ok(entries) = fs::read_dir(dec_dir) {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if let Some(stem) = name.strip_suffix(".ts") {
                if let Ok(index) = stem.parse::<u32>() {
                    if valid_dec_file(dec_dir, index) {
                        found.insert(index, String::new());
                    }
                }
            }
        }
    }
    found
}

pub fn fetch_and_decode(
    client: &reqwest::blocking::Client,
    enc_dir: &Path,
    dec_dir: &Path,
    segment: &Segment,
) -> Result<()> {
    let enc_path = enc_dir.join(&segment.file);
    let mut blob = match fs::read(&enc_path) {
        Ok(bytes) if bytes.len() % 16 == 0 && bytes.len() as u64 >= MIN_SEGMENT => bytes,
        _ => Vec::new(),
    };
    if blob.is_empty() {
        blob = client
            .get(&segment.url)
            .send()
            .with_context(|| format!("fetch {}", segment.url))?
            .error_for_status()
            .with_context(|| format!("fetch {}", segment.url))?
            .bytes()
            .with_context(|| format!("read body of {}", segment.url))?
            .to_vec();
        if !(blob.len() % 16 == 0 && blob.len() as u64 >= MIN_SEGMENT) {
            bail!("{} returned {} unusable bytes", segment.url, blob.len());
        }
        fs::write(&enc_path, &blob)?;
    }

    let key = key_from_text(&segment.key)?;
    let mask = mask_from_filename(&segment.file);
    let plain = match decrypt(&blob, &key, &mask, &segment.file) {
        Ok(plain) => plain,
        Err(first) => {
            // The cached ciphertext may be stale; refetch once before giving up on this key.
            let fresh = client.get(&segment.url).send()?.error_for_status()?.bytes()?.to_vec();
            fs::write(&enc_path, &fresh)?;
            decrypt(&fresh, &key, &mask, &segment.file)
                .with_context(|| format!("after refetch ({first})"))?
        }
    };

    let target = dec_dir.join(format!("{:06}.ts", segment.index));
    let partial = target.with_extension("ts.tmp");
    fs::write(&partial, &plain)?;
    fs::rename(&partial, &target)?;
    Ok(())
}
