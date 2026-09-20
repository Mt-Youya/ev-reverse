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

/// Download and decrypt every task using two independent global worker limits.
///
/// `on_decoded` is called in completion order, not lesson order. Its implementation must retain
/// out-of-order data until the corresponding lesson's next index is available.
pub async fn run(
    tasks: Vec<SegmentTask>,
    download_jobs: usize,
    decrypt_jobs: usize,
    mut on_decoded: impl FnMut(DecodedSegment) -> Result<()>,
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
            let _download = downloads.acquire_owned().await.expect("download queue stays open");
            download_segment(client, task.remote, task.target.clone()).await?;
            drop(_download);

            let _decrypt = decrypts.acquire_owned().await.expect("decrypt queue stays open");
            let bytes = tokio::task::spawn_blocking(move || {
                let encrypted = std::fs::read(&task.target)
                    .with_context(|| format!("read downloaded segment {}", task.target.display()))?;
                // The signed list has key material but not an encrypted fingerprint.  Record the
                // fingerprint at the only trustworthy point: immediately after this task has
                // downloaded (or resumed) its own ciphertext.
                let mut manifest = task.manifest;
                manifest.encrypted_sha256 = sha256_hex(&encrypted);
                let bytes = decode_segment(&encrypted, &manifest)?;
                Ok::<_, anyhow::Error>(DecodedSegment {
                    lesson: task.lesson,
                    index: manifest.index,
                    bytes,
                })
            })
            .await
            .context("decrypt worker panicked")??;
            Ok::<_, anyhow::Error>(bytes)
        });
    }

    while let Some(decoded) = pending.next().await {
        on_decoded(decoded?)?;
    }
    Ok(())
}
