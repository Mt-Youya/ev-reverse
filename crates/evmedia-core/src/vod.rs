//! Complete VOD membership comes from the original M3U8, not a playback window.
use anyhow::{bail, Context, Result};
use std::collections::HashSet;

#[derive(Debug)]
pub struct Vod {
    pub names: Vec<String>,
    pub seconds: f64,
}

impl Vod {
    pub fn parse(text: &str) -> Result<Self> {
        let mut lines = text.trim().lines().map(str::trim);
        if lines.next() != Some("#EXTM3U") {
            bail!("missing M3U8 header");
        }
        let mut names = Vec::new();
        let mut seen = HashSet::new();
        let mut seconds = 0.0;
        let mut duration = None;
        let mut ended = false;
        for line in lines {
            if line == "#EXT-X-ENDLIST" {
                ended = true;
                break;
            }
            if let Some(sequence) = line.strip_prefix("#EXT-X-MEDIA-SEQUENCE:") {
                if sequence != "0" {
                    bail!("playlist does not start at segment zero");
                }
            } else if let Some(value) = line.strip_prefix("#EXTINF:") {
                if duration.is_some() {
                    bail!("duration without a segment");
                }
                let value: f64 = value.split(',').next().unwrap_or("").parse()?;
                if !value.is_finite() || value <= 0.0 {
                    bail!("invalid segment duration");
                }
                duration = Some(value);
            } else if !line.is_empty() && !line.starts_with('#') {
                let name = line.split('?').next().unwrap_or("").rsplit('/').next().unwrap_or("");
                if !segment_name(name) || !seen.insert(name.to_string()) {
                    bail!("invalid or duplicate segment filename");
                }
                seconds += duration.take().context("segment has no duration")?;
                names.push(name.to_string());
            } else if line.starts_with("#EXT-X-STREAM-INF:") || line.starts_with("#EXT-X-BYTERANGE:") {
                bail!("master and byte-range playlists are not supported");
            }
        }
        if !ended || names.is_empty() || duration.is_some() || !seconds.is_finite() {
            bail!("playlist is incomplete: requires all segments and ENDLIST");
        }
        Ok(Self { names, seconds })
    }

    pub fn validate_batch(&self, offset: usize, list: &crate::playlist::Playlist) -> Result<()> {
        let ordered = list.ordered()?;
        let expected = self.names.get(offset..(offset + 100).min(self.names.len()))
            .context("batch offset outside playlist")?;
        if ordered.len() != expected.len() || ordered.iter().zip(expected).enumerate()
            .any(|(i, ((index, name), wanted))| *index != i as u32 || name != wanted) {
            bail!("API reply does not contain exactly the requested segments in order");
        }
        Ok(())
    }
}

fn segment_name(name: &str) -> bool {
    let Some(stem) = name.strip_suffix(".ts") else { return false; };
    let Some((account, uuid)) = stem.split_once('-') else { return false; };
    !account.is_empty() && account.bytes().all(|b| b.is_ascii_digit()) && uuid.len() == 36
        && uuid.bytes().enumerate().all(|(i, b)| {
            if [8, 13, 18, 23].contains(&i) { b == b'-' } else { b.is_ascii_hexdigit() }
        })
}
