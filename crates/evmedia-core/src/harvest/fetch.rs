//! Fetching one segment and turning it into a decrypted file on disk.
//!
//! Ciphertext is cached under `enc/` and plaintext under `dec/`, both keyed by segment index,
//! so an interrupted run resumes rather than re-downloading. Writes land on a temporary name
//! and are renamed into place, because the merge step treats any file it finds as complete.
//!
//! The two halves are separable, and the split is what keeps the harvest loop honest:
//! [`download_missing`] takes no key at all, and [`fetch_and_decode`] needs one. A key is derived
//! by testing it against the segment's bytes, so a download that waited for a key would wait
//! forever — see `grab`'s module comment.

use super::Segment;
use crate::crypto::{decrypt, key_from_text, mask_from_filename};
use anyhow::{bail, Context, Result};
use std::{collections::BTreeMap, collections::BTreeSet, collections::HashMap, fs, path::Path};

/// Smallest plausible segment: two 188-byte packets.
const MIN_SEGMENT: u64 = 376;

/// True when a blob is shaped like segment ciphertext: AES-block aligned and at least two packets.
fn plausible(blob: &[u8]) -> bool {
    blob.len() % 16 == 0 && blob.len() as u64 >= MIN_SEGMENT
}

/// The cached ciphertext for a segment, when a complete one is already on disk.
fn cached_cipher(path: &Path) -> Option<Vec<u8>> {
    match fs::read(path) {
        Ok(bytes) if plausible(&bytes) => Some(bytes),
        _ => None,
    }
}

/// Copy complete local ciphertext without modifying the player's download cache.
pub fn import_cache(cache: &Path, enc_dir: &Path, files: &HashMap<String, u32>) -> Result<()> {
    for file in files.keys() {
        let target = enc_dir.join(file);
        if fs::metadata(&target).is_ok_and(|meta| meta.len() >= MIN_SEGMENT && meta.len() % 16 == 0) {
            continue;
        }
        let Some(bytes) = cached_cipher(&cache.join(file)) else { continue };
        let temporary = target.with_extension("ts.tmp");
        fs::write(&temporary, bytes)?;
        fs::rename(&temporary, &target)?;
    }
    Ok(())
}

/// Fetch one segment's ciphertext, rejecting a body that cannot be segment ciphertext.
fn download(client: &reqwest::blocking::Client, url: &str) -> Result<Vec<u8>> {
    let blob = client
        .get(url)
        .send()
        .with_context(|| format!("fetch {url}"))?
        .error_for_status()
        .with_context(|| format!("fetch {url}"))?
        .bytes()
        .with_context(|| format!("read body of {url}"))?
        .to_vec();
    if !plausible(&blob) {
        bail!("{url} returned {} unusable bytes", blob.len());
    }
    Ok(blob)
}

/// Download the ciphertext for every known URL that is not already cached, returning how many
/// were fetched and how many could not be.
///
/// This runs before any key is known and takes none. Downloading does not need a key — only
/// *decrypting* does — and the key can only be derived from the bytes this function puts on
/// disk, so this has to happen first. An expired signed URL is an ordinary outcome rather than
/// an error: the player reissues them as it plays, so a later poll picks the segment up.
///
/// `done` names the segments that are already decrypted. They are skipped outright. Without
/// that, a resumed run would re-request every URL of a lesson it had already finished, and an
/// unreachable host turns each of those into a wait — the work is wasted even when it succeeds,
/// because a decrypted segment's ciphertext has nothing left to teach.
pub fn download_missing(
    client: &reqwest::blocking::Client,
    enc_dir: &Path,
    urls: &HashMap<String, String>,
    done: &BTreeSet<String>,
) -> (usize, usize) {
    let (mut fetched, mut failed) = (0, 0);
    for (file, url) in urls {
        if done.contains(file) {
            continue;
        }
        let path = enc_dir.join(file);
        if cached_cipher(&path).is_some() {
            continue;
        }
        let body = download(client, url);
        match body {
            Ok(blob) if fs::write(&path, &blob).is_ok() => fetched += 1,
            _ => failed += 1,
        }
    }
    (fetched, failed)
}

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
    // Normally already on disk: the loop downloads before it asks for keys, because a key can
    // only be derived from these bytes.
    let mut blob = cached_cipher(&enc_path).unwrap_or_default();
    if blob.is_empty() {
        let Some(url) = segment.url.as_deref() else {
            bail!(
                "{} has no cached ciphertext and the player is no longer offering a URL for it",
                segment.file
            );
        };
        let fresh = download(client, url)?;
        fs::write(&enc_path, &fresh)?;
        blob = fresh;
    }

    let key = key_from_text(&segment.key)?;
    let mask = mask_from_filename(&segment.file);
    let plain = match decrypt(&blob, &key, &mask, &segment.file) {
        Ok(plain) => plain,
        Err(first) => {
            // The cached ciphertext may be stale; refetch once before giving up on this key. A
            // segment whose URL the player has released has nothing to refetch from, and the
            // original error is the one worth reporting.
            let Some(url) = segment.url.as_deref() else {
                return Err(first).with_context(|| {
                    format!("{} cannot be refetched: no URL is on offer", segment.file)
                });
            };
            let fresh = download(client, url)?;
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
