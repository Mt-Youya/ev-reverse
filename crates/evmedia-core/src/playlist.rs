//! The segment list the player fetches before it plays, and the keys it implies.
//!
//! One response carries everything a lesson needs: `d_p` is the host, and every entry in `k_l`
//! gives a segment's `idx` (its *lesson index*), its signed URL (`sf`) and its `tk`. Since
//! `crypto::key_from_tk` turns a `tk` and a filename into the key, this file alone is enough to
//! build a manifest, which is what makes a lesson capturable without playing it.
//!
//! Response bodies on the wire are encrypted, and this contract is what the *player* holds after
//! decrypting one; `tools/parser-tools/capture_all.py` records them as `captured/inflated/*.json`
//! and `tools/parser-tools/api_summary.py` describes the envelopes they arrived in.

use crate::crypto::key_from_tk;
use crate::decode::{build_manifest_of, CaptureManifest};
use anyhow::{bail, Context, Result};
use evmedia_contract::Reporter;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::Path;

#[derive(Debug, Deserialize)]
pub struct Playlist {
    /// Host the segments live on. The `sf` paths are relative to it.
    #[serde(default)]
    pub d_p: String,
    pub k_l: Vec<PlaylistEntry>,
}

#[derive(Debug, Deserialize)]
pub struct PlaylistEntry {
    pub idx: u32,
    /// The signed path, `/119354-<uuid>.ts?bid=..&sid=..&t=..&v=..&sign=..`.
    pub sf: String,
    pub tk: String,
}

impl PlaylistEntry {
    /// The segment's filename, which is what the key derivation and the XOR mask are over.
    pub fn filename(&self) -> Result<&str> {
        let path = self.sf.split('?').next().unwrap_or(&self.sf);
        match path.rsplit('/').next() {
            Some(name) if !name.is_empty() => Ok(name),
            _ => bail!("segment path has no filename: {}", self.sf),
        }
    }

    /// The signed URL, host included.
    pub fn url(&self, host: &str) -> String {
        if self.sf.starts_with("http") {
            return self.sf.clone();
        }
        format!("{}{}", host.trim_end_matches('/'), self.sf)
    }
}

impl Playlist {
    /// filename -> (lesson index, key), which is what `decode::build_manifest_of` wants.
    ///
    /// A duplicate filename is an error rather than a last-one-wins: two entries claiming the same
    /// file would mean two different keys for one segment, and nothing downstream could tell which
    /// was used.
    pub fn keys(&self) -> Result<HashMap<String, (u32, String)>> {
        if self.k_l.is_empty() {
            bail!("segment list holds no segments");
        }
        let mut keys = HashMap::with_capacity(self.k_l.len());
        for entry in &self.k_l {
            let filename = entry.filename()?.to_string();
            let key = key_from_tk(&entry.tk, &filename)
                .with_context(|| format!("derive key for {filename}"))?;
            if keys.insert(filename.clone(), (entry.idx, key)).is_some() {
                bail!("segment list names {filename} twice");
            }
        }
        Ok(keys)
    }

    /// The lesson's segments in playback order: `(index, filename)`.
    pub fn ordered(&self) -> Result<Vec<(u32, String)>> {
        let mut ordered: Vec<(u32, String)> = self
            .k_l
            .iter()
            .map(|entry| Ok((entry.idx, entry.filename()?.to_string())))
            .collect::<Result<Vec<_>>>()?;
        ordered.sort_by_key(|(index, _)| *index);
        Ok(ordered)
    }
}

#[derive(Debug, Serialize)]
pub struct PlaylistSummary {
    pub host: String,
    pub segment_count: usize,
    pub first_index: u32,
    pub last_index: u32,
}

/// Check what a list describes without touching any input files.
pub fn summarize(list: &Playlist) -> Result<PlaylistSummary> {
    if list.k_l.is_empty() {
        bail!("segment list holds no segments");
    }
    let mut indexes: Vec<u32> = list.k_l.iter().map(|entry| entry.idx).collect();
    indexes.sort_unstable();
    let contiguous = indexes.iter().enumerate().all(|(at, index)| *index == at as u32);
    if !contiguous {
        bail!(
            "segment indexes are not a contiguous 0..n range ({} of them, {}..{}); the list is \
             partial and a merge from it would be the wrong length",
            indexes.len(),
            indexes[0],
            indexes[indexes.len() - 1]
        );
    }
    Ok(PlaylistSummary {
        host: list.d_p.clone(),
        segment_count: indexes.len(),
        first_index: indexes[0],
        last_index: indexes[indexes.len() - 1],
    })
}

/// `derive`: turn a captured segment list plus the downloaded ciphertext into a manifest.
///
/// This is `capture-ev` with the player taken out. The segments still have to be *fetched* — the
/// manifest records each one's fingerprint — but nothing has to be *played*, which is the whole
/// difference: the keys come from the list, not from a running process.
pub fn run(playlist_path: &Path, input: &Path, output: &Path, reporter: &Reporter) -> Result<()> {
    let list: Playlist = crate::read_json(playlist_path)?;
    let summary = summarize(&list)?;
    reporter.info(format!(
        "{} segment(s), indexes {}..{}, from {}",
        summary.segment_count, summary.first_index, summary.last_index, summary.host
    ));

    let keys = list.keys()?;

    // The list decides which segments this lesson has. The directory those files sit in usually
    // holds every lesson the player ever downloaded, so the names come from the list and nowhere
    // else.
    let names: Vec<String> = list.ordered()?.into_iter().map(|(_, name)| name).collect();
    let manifest: CaptureManifest = build_manifest_of(input, names, &keys, TOOL)?;

    if let Some(parent) = output.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(output, serde_json::to_vec_pretty(&manifest)?)?;
    reporter.info(format!(
        "wrote {} derived segment(s) to {}",
        manifest.segment_count,
        output.display()
    ));
    Ok(())
}

/// Named in the manifest, and checked by `decode-ev`, which refuses a manifest from an unsupported
/// collector.
pub const TOOL: &str = "EVPlayer2 5.0.5 derived-from-segment-list";
