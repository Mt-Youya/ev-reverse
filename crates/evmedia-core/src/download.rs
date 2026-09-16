//! The `DownloadManifest v1` JSON contract: authorized segment URLs, downloaded concurrently
//! and written atomically so an interrupted run resumes instead of re-fetching.

use crate::paths::safe_relative;
use anyhow::{bail, Context, Result};
use evmedia_contract::{Event, Reporter, SegmentState, Stage};
use futures_util::{stream::FuturesUnordered, StreamExt};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, path::PathBuf, sync::Arc};
use tokio::{io::AsyncWriteExt, sync::Semaphore};
use url::Url;

#[derive(Debug, Deserialize)]
pub struct DownloadManifest {
    pub version: u32,
    /// Carried for the UI; the download path itself does not branch on it.
    #[allow(dead_code)]
    pub course_title: String,
    pub videos: Vec<RemoteVideo>,
}

#[derive(Debug, Deserialize)]
pub struct RemoteVideo {
    #[allow(dead_code)]
    pub id: String,
    pub relative_path: String,
    pub segments: Vec<RemoteSegment>,
}

#[derive(Debug, Deserialize)]
pub struct RemoteSegment {
    pub index: u32,
    pub url: String,
    #[serde(default)]
    pub headers: BTreeMap<String, String>,
    #[serde(default)]
    pub sha256: Option<String>,
    /// Written instead of the default `NNNNN.bin` when the manifest came from a segment list. The
    /// key derivation and the XOR mask are both over the filename, so keeping the player's own
    /// name here is what lets `derive` read the download directory untouched.
    #[serde(default)]
    pub filename: Option<String>,
}

/// `download` takes either a hand-written manifest or the segment list `fetch` just wrote, so a
/// lesson goes from signed list to files on disk with nothing in between.
pub fn load_input(path: &std::path::Path) -> Result<DownloadManifest> {
    let value: serde_json::Value = crate::read_json(path)?;
    if value.get("k_l").is_some() {
        let list: crate::playlist::Playlist = serde_json::from_value(value)
            .with_context(|| format!("parse {} as a segment list", path.display()))?;
        return list.to_download_manifest();
    }
    serde_json::from_value(value).with_context(|| format!("parse {} as a download manifest", path.display()))
}

async fn download_segment(client: reqwest::Client, segment: RemoteSegment, target: PathBuf) -> Result<()> {
    if !matches!(Url::parse(&segment.url)?.scheme(), "https" | "http") {
        bail!("unsupported URL scheme");
    }
    if let Some(parent) = target.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    if target.exists() {
        if let Some(expected) = &segment.sha256 {
            if hex::encode(Sha256::digest(tokio::fs::read(&target).await?)) == expected.to_lowercase() {
                return Ok(());
            }
        } else {
            return Ok(());
        }
    }
    let partial = target.with_extension("part");
    let mut request = client.get(&segment.url);
    for (name, value) in &segment.headers {
        request = request.header(name, value);
    }
    let response = request.send().await?.error_for_status()?;
    let mut stream = response.bytes_stream();
    let mut file = tokio::fs::File::create(&partial).await?;
    let mut hasher = Sha256::new();
    while let Some(chunk) = stream.next().await {
        let bytes = chunk?;
        hasher.update(&bytes);
        file.write_all(&bytes).await?;
    }
    file.flush().await?;
    if let Some(expected) = &segment.sha256 {
        let actual = hex::encode(hasher.finalize());
        if actual != expected.to_lowercase() {
            tokio::fs::remove_file(&partial).await?;
            bail!("checksum mismatch for {}", segment.url);
        }
    }
    tokio::fs::rename(partial, target).await?;
    Ok(())
}

pub async fn download_all(
    manifest: DownloadManifest,
    output: PathBuf,
    parallel: usize,
    reporter: &Reporter,
) -> Result<()> {
    if manifest.version != 1 {
        bail!("unsupported download manifest version");
    }
    let client = reqwest::Client::builder().user_agent("evmedia/0.1").build()?;
    let gate = Arc::new(Semaphore::new(parallel.max(1)));
    reporter.event(&Event::Stage {
        name: Stage::Download,
        state: evmedia_contract::StageState::Begin,
        detail: String::new(),
    });

    let total: usize = manifest.videos.iter().map(|video| video.segments.len()).sum();
    let mut started = 0usize;
    let mut completed = 0usize;
    let mut failures: Vec<String> = Vec::new();
    // One unordered set rather than spawn-then-join: each segment reports the moment it lands,
    // which is what keeps the GUI's counter moving on a slow link.
    let mut pending = FuturesUnordered::new();

    for video in manifest.videos {
        let folder = output.join(safe_relative(&video.relative_path)?);
        for segment in video.segments {
            if reporter.stopped() {
                break;
            }
            let index = segment.index;
            let gate = gate.clone();
            let client = client.clone();
            let target = folder.join(match &segment.filename {
                Some(name) => safe_relative(name)?,
                None => PathBuf::from(format!("{:05}.bin", segment.index)),
            });
            let url = segment.url.clone();
            started += 1;
            pending.push(async move {
                // Acquire while futures are polled, otherwise scheduling more than `parallel`
                // segments waits for a permit that no pending download can release.
                let _permit = gate.acquire_owned().await.expect("download gate stays open");
                (index, url, download_segment(client, segment, target).await)
            });
        }
    }

    while let Some((index, url, outcome)) = pending.next().await {
        match outcome {
            Ok(()) => {
                completed += 1;
                reporter.event(&Event::Segment {
                    index,
                    file: url,
                    state: SegmentState::Done,
                    attempt: 1,
                    error: None,
                });
            }
            Err(error) => {
                let message = error.to_string();
                reporter.event(&Event::Segment {
                    index,
                    file: url.clone(),
                    state: SegmentState::Failed,
                    attempt: 1,
                    error: Some(message.clone()),
                });
                failures.push(format!("{url}: {message}"));
            }
        }
    }

    reporter.event(&Event::Stage {
        name: Stage::Download,
        state: evmedia_contract::StageState::End,
        detail: format!("{completed}/{started} segment(s) of {total}"),
    });
    if !failures.is_empty() {
        bail!("{} download task(s) failed:\n{}", failures.len(), failures.join("\n"));
    }
    if reporter.stopped() {
        reporter.info("stopped on request; completed segments are kept");
        return Ok(());
    }
    // Wording is part of the CLI's contract with existing scripts; do not embellish it.
    reporter.info(format!("Download completed: {}", output.display()));
    Ok(())
}
