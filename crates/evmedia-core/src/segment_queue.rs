//! A bounded global queue from encrypted segment download to plaintext segment bytes.
//!
//! The queue deliberately has no lesson-level barrier: every task acquires a network permit,
//! then a CPU permit, and reaches the caller as soon as its own bytes are ready. The caller owns
//! ordering, because only it knows which lesson's next index may be appended to which output.

use crate::{
    crypto::sha256_hex,
    decode::{decode_segment, EvSegment},
    download::{download_segment, RemoteSegment},
};
use anyhow::{Context, Result};
use futures_util::{stream::FuturesUnordered, StreamExt};
use std::{path::PathBuf, sync::Arc};
use tokio::sync::Semaphore;

pub struct SegmentTask {
    /// Stable owner chosen by the batch coordinator, normally `course:file`.
    pub lesson: String,
    /// Where the encrypted segment is resumed or atomically written.
    pub target: PathBuf,
    pub remote: RemoteSegment,
    pub manifest: EvSegment,
}

pub struct DecodedSegment {
    pub lesson: String,
    pub index: u32,
    pub bytes: Vec<u8>,
}

/// A task's terminal outcome. A bad CDN response belongs to one segment, not to every lesson in
/// the batch, so the coordinator receives it instead of the queue aborting all pending tasks.
pub struct SegmentOutcome {
    pub lesson: String,
    pub index: u32,
    pub result: Result<Vec<u8>, String>,
}

/// Download and decrypt every task using two independent global worker limits.
///
/// `on_segment` is called in completion order, not lesson order. Its implementation must retain
/// out-of-order data until the corresponding lesson's next index is available, and can isolate a
/// permanently failed segment to just its own lesson.
pub async fn run(
    tasks: Vec<SegmentTask>,
    download_jobs: usize,
    decrypt_jobs: usize,
    mut on_segment: impl FnMut(SegmentOutcome) -> Result<()>,
) -> Result<()> {
    let client = reqwest::Client::builder().user_agent("evmedia/0.1").build()?;
    let downloads = Arc::new(Semaphore::new(download_jobs.max(1)));
    let decrypts = Arc::new(Semaphore::new(decrypt_jobs.max(1)));
    let mut pending = FuturesUnordered::new();

    for task in tasks {
        let client = client.clone();
        let downloads = downloads.clone();
        let decrypts = decrypts.clone();
        pending.push(async move {
            let lesson = task.lesson.clone(); let index = task.manifest.index;
            let mut last = None;
            for attempt in 0..3 {
                let outcome = async {
                    let _download = downloads.clone().acquire_owned().await.expect("download queue stays open");
                    download_segment(client.clone(), task.remote.clone(), task.target.clone()).await?;
                    drop(_download);
                    let _decrypt = decrypts.clone().acquire_owned().await.expect("decrypt queue stays open");
                    let target = task.target.clone(); let mut manifest = task.manifest.clone();
                    tokio::task::spawn_blocking(move || {
                        let encrypted = std::fs::read(&target).with_context(|| format!("read downloaded segment {}", target.display()))?;
                        // The signed list has key material but not an encrypted fingerprint.
                        manifest.encrypted_sha256 = sha256_hex(&encrypted);
                        decode_segment(&encrypted, &manifest)
                    }).await.context("decrypt worker panicked")?
                }.await;
                match outcome { Ok(bytes) => return SegmentOutcome { lesson, index, result: Ok(bytes) }, Err(error) => last = Some(error) }
                if attempt < 2 { tokio::time::sleep(std::time::Duration::from_secs(1 << attempt)).await; }
            }
            SegmentOutcome { lesson, index, result: Err(last.expect("three attempts ran").to_string()) }
        });
    }

    while let Some(outcome) = pending.next().await {
        on_segment(outcome)?;
    }
    Ok(())
}
