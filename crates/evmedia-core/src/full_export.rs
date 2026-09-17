//! Complete video export, independent of Python and the player's playback position.
use crate::{api, decode, download, playlist::Playlist, vod::Vod};
use anyhow::{bail, Context, Result};
use evmedia_contract::Reporter;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::{fs, io::Write, path::{Path, PathBuf}};

#[derive(Deserialize)]
pub struct Session {
    pub token: String,
    pub playkey: String,
}

impl Session {
    pub fn load(path: &Path, vod: &Vod) -> Result<Self> {
        Self::parse(&fs::read_to_string(path)?, vod)
    }

    pub fn parse(text: &str, vod: &Vod) -> Result<Self> {
        if let Ok(session) = serde_json::from_str::<Self>(text) {
            if session.token.is_empty() || session.playkey.is_empty() { bail!("empty session credentials"); }
            return Ok(session);
        }
        let mut token = None;
        let mut playkey = None;
        for line in text.lines() {
            let Ok(event) = serde_json::from_str::<serde_json::Value>(line) else { continue; };
            if let Some(value) = event["headers"]["authorization"].as_str() {
                token = Some(value.to_string());
            }
            if let Some(headers) = event["hdrs"].as_array() {
                for header in headers {
                    if header[0] == "authorization" {
                        token = header[1].as_str().map(str::to_string);
                    }
                }
            }
            if let Some(input) = event.get("input").or_else(|| event.get("md5_input")).and_then(|v| v.as_str()) {
                if let Ok((key, list)) = api::fields_from_preimage(input) {
                    if list.split(',').all(|s| s.rsplit('|').next().is_some_and(|n| vod.names.iter().any(|v| v == n))) {
                        playkey = Some(key);
                    }
                }
            }
        }
        Ok(Self { token: token.context("capture has no authorization header")?,
                  playkey: playkey.context("capture has no play key matching the complete playlist")? })
    }
}

pub struct Options {
    pub output: PathBuf,
    pub work: PathBuf,
    pub cache: Option<PathBuf>,
    pub jobs: usize,
    pub ffmpeg: String,
    pub ffprobe: String,
}

pub async fn fetch_all(vod: &Vod, session: &Session, reporter: &Reporter) -> Result<Playlist> {
    let mut full = Playlist { d_p: String::new(), k_l: Vec::new() };
    for (batch, names) in vod.names.chunks(100).enumerate() {
        if reporter.stopped() { bail!("export stopped"); }
        let liststr = names.iter().enumerate().map(|(i, n)| format!("{i}|0|{n}"))
            .collect::<Vec<_>>().join(",");
        let response = api::fetch_list(&api::ListRequest::new(&session.playkey, liststr), &session.token).await?;
        let list: Playlist = serde_json::from_value(response)?;
        vod.validate_batch(batch * 100, &list)?;
        for mut entry in list.k_l {
            entry.sf = entry.url(&list.d_p);
            entry.idx += (batch * 100) as u32;
            full.k_l.push(entry);
        }
        reporter.info(format!("取得分段密钥 {}/{}", full.k_l.len(), vod.names.len()));
    }
    Ok(full)
}

pub async fn run(text: &str, session: &Session, options: &Options, reporter: &Reporter) -> Result<()> {
    let vod = Vod::parse(text)?;
    if options.output.exists() { bail!("output already exists: {}", options.output.display()); }
    let extension = options.output.extension().and_then(|s| s.to_str()).unwrap_or("");
    if !matches!(extension, "mp4" | "mkv") { bail!("output must end in .mp4 or .mkv"); }
    let identity = hex::encode(Sha256::digest(vod.names.join("\n").as_bytes()));
    let work = options.work.join(&identity[..16]);
    fs::create_dir_all(&work)?;
    // A process-owned work lock prevents two exports from changing the same partial files.
    let lock = WorkLock::acquire(&work)?;
    fs::write(work.join("original.m3u8"), text)?;
    let list = fetch_all(&vod, session, reporter).await?;
    fs::write(work.join("list.json"), serde_json::to_vec_pretty(&list)?)?;
    let enc = work.join("enc");
    fs::create_dir_all(&enc)?;
    let keys = list.keys()?;
    // Only reuse ciphertext if it actually decrypts with this video's derived key.
    if let Some(cache) = &options.cache {
        for name in &vod.names {
            let target = enc.join(name);
            if target.exists() { continue; }
            let source = cache.join(name);
            if let Ok(data) = fs::read(&source) {
                if valid_cipher(&data, name, &keys[name].1) {
                    fs::write(&target, data)?;
                }
            }
        }
    }
    for name in &vod.names {
        let target = enc.join(name);
        if target.exists() && !valid_cipher(&fs::read(&target)?, name, &keys[name].1) {
            // Preserve suspect bytes for diagnosis; let the downloader replace the cache entry.
            fs::rename(&target, target.with_extension("invalid"))?;
        }
    }
    download::download_all(list.to_download_manifest()?, enc.clone(), options.jobs, reporter).await?;
    if reporter.stopped() { bail!("export stopped"); }
    let manifest = decode::build_manifest_of(&enc, vod.names.clone(), &keys, crate::playlist::TOOL)?;
    fs::write(work.join("manifest.json"), serde_json::to_vec_pretty(&manifest)?)?;
    let partial_ts = work.join("lesson.partial.ts");
    let mut merged = fs::File::create(&partial_ts)?;
    for item in &manifest.segments {
        if reporter.stopped() { bail!("export stopped"); }
        merged.write_all(&decode::decode_segment(&fs::read(enc.join(&item.file))?, item)?)?;
    }
    merged.sync_all()?;
    drop(merged);
    let ts = work.join("lesson.ts");
    fs::rename(&partial_ts, &ts)?;
    crate::verified_media::publish(&ts, &vod, options, &work, reporter)?;
    drop(lock);
    Ok(())
}

fn valid_cipher(data: &[u8], name: &str, key: &str) -> bool {
    let Ok(key) = crate::crypto::key_from_hex(&hex::encode(key.as_bytes())) else { return false; };
    crate::crypto::decrypt(data, &key, &crate::crypto::mask_from_filename(name), name).is_ok()
}

struct WorkLock { _file: fs::File }
impl WorkLock {
    fn acquire(work: &Path) -> Result<Self> {
        let path = work.join("export.lock");
        let mut options = fs::OpenOptions::new();
        options.write(true).create(true).truncate(true);
        #[cfg(windows)] {
            use std::os::windows::fs::OpenOptionsExt;
            options.share_mode(0);
        }
        let mut file = options.open(&path).context("another export may be using this work directory")?;
        write!(file, "{}", std::process::id())?;
        Ok(Self { _file: file })
    }
}
