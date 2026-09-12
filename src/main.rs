use aes::Aes256;
use anyhow::{bail, Context, Result};
use clap::{Parser, Subcommand};
use ecb::cipher::{block_padding::NoPadding, BlockDecryptMut, KeyInit};
use futures_util::StreamExt;
use md5::Digest as Md5Digest;
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::{
    collections::BTreeMap,
    fs,
    io::{Read, Write},
    path::{Component, Path, PathBuf},
    sync::Arc,
};
use tokio::{io::AsyncWriteExt, sync::Semaphore};
use url::Url;

#[cfg(windows)]
mod evplayer_windows;

#[derive(Parser)]
#[command(
    name = "evmedia",
    version,
    about = "Portable course-media catalog and verified download core"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Print a course/video directory tree from a JSON catalog.
    Tree { catalog: PathBuf },
    /// Download all listed segments concurrently with resume-safe atomic files.
    Download {
        manifest: PathBuf,
        output: PathBuf,
        #[arg(long, default_value_t = 8)]
        parallel: usize,
    },
    /// Decode an EVPlayer2 5.0.5 segment ZIP/directory using a live-captured manifest.
    DecodeEv {
        input: PathBuf,
        manifest: PathBuf,
        output: PathBuf,
    },
    /// Create a video-specific EVPlayer2 5.0.5 manifest from an active Windows process.
    CaptureEv {
        #[arg(long)]
        pid: u32,
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Report adapters compiled into this portable core.
    Adapters,
}

#[derive(Debug, Deserialize, Serialize)]
struct Catalog {
    title: String,
    roots: Vec<CatalogNode>,
}

#[derive(Debug, Deserialize, Serialize)]
struct CatalogNode {
    id: String,
    title: String,
    #[serde(default)]
    kind: NodeKind,
    #[serde(default)]
    children: Vec<CatalogNode>,
    #[serde(default)]
    video: Option<VideoRef>,
}

#[derive(Debug, Deserialize, Serialize, Default)]
#[serde(rename_all = "snake_case")]
enum NodeKind {
    #[default]
    Folder,
    Video,
}

#[derive(Debug, Deserialize, Serialize)]
struct VideoRef {
    id: String,
    duration_seconds: Option<f64>,
    source: String,
}

#[derive(Debug, Deserialize)]
struct DownloadManifest {
    version: u32,
    course_title: String,
    videos: Vec<RemoteVideo>,
}

#[derive(Debug, Deserialize)]
struct RemoteVideo {
    id: String,
    relative_path: String,
    segments: Vec<RemoteSegment>,
}

#[derive(Debug, Deserialize)]
struct RemoteSegment {
    index: u32,
    url: String,
    #[serde(default)]
    headers: BTreeMap<String, String>,
    #[serde(default)]
    sha256: Option<String>,
}

#[derive(Debug, Deserialize)]
struct EvManifest {
    #[serde(default)]
    tool: String,
    #[serde(default)]
    variant: String,
    segments: Vec<EvSegment>,
}

#[derive(Debug, Deserialize)]
struct EvSegment {
    index: u32,
    file: String,
    key_hex: String,
    xor_mask_hex: String,
    encrypted_sha256: String,
}

fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T> {
    serde_json::from_slice(&fs::read(path).with_context(|| format!("read {}", path.display()))?)
        .with_context(|| format!("parse {}", path.display()))
}

fn print_tree(node: &CatalogNode, depth: usize) {
    let indent = "  ".repeat(depth);
    match &node.video {
        Some(video) => println!("{indent}▶ {} [{}]", node.title, video.id),
        None => println!("{indent}▾ {}", node.title),
    }
    for child in &node.children {
        print_tree(child, depth + 1);
    }
}

fn safe_relative(path: &str) -> Result<PathBuf> {
    let value = Path::new(path);
    if value.is_absolute()
        || value
            .components()
            .any(|part| matches!(part, Component::ParentDir | Component::Prefix(_)))
    {
        bail!("unsafe relative path: {path}");
    }
    Ok(value.to_path_buf())
}

async fn download_segment(
    client: reqwest::Client,
    segment: RemoteSegment,
    target: PathBuf,
) -> Result<()> {
    if !matches!(Url::parse(&segment.url)?.scheme(), "https" | "http") {
        bail!("unsupported URL scheme");
    }
    if let Some(parent) = target.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    if target.exists() {
        if let Some(expected) = &segment.sha256 {
            if hex::encode(Sha256::digest(tokio::fs::read(&target).await?))
                == expected.to_lowercase()
            {
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

async fn download_all(manifest: DownloadManifest, output: PathBuf, parallel: usize) -> Result<()> {
    if manifest.version != 1 {
        bail!("unsupported download manifest version");
    }
    let client = reqwest::Client::builder()
        .user_agent("evmedia/0.1")
        .build()?;
    let gate = Arc::new(Semaphore::new(parallel.max(1)));
    let mut tasks = Vec::new();
    for video in manifest.videos {
        let folder = output.join(safe_relative(&video.relative_path)?);
        for segment in video.segments {
            let permit = gate.clone().acquire_owned().await?;
            let client = client.clone();
            let target = folder.join(format!("{:05}.bin", segment.index));
            tasks.push(tokio::spawn(async move {
                let _permit = permit;
                download_segment(client, segment, target).await
            }));
        }
    }
    let mut failures = Vec::new();
    for task in tasks {
        if let Err(error) = task.await? {
            failures.push(error.to_string());
        }
    }
    if !failures.is_empty() {
        bail!(
            "{} download task(s) failed:\n{}",
            failures.len(),
            failures.join("\n")
        );
    }
    println!("Download completed: {}", output.display());
    Ok(())
}

fn find_input_bytes(input: &Path, name: &str) -> Result<Vec<u8>> {
    if input.is_dir() {
        return fs::read(input.join(name)).with_context(|| format!("read segment {name}"));
    }
    let file = fs::File::open(input)?;
    let mut archive = zip::ZipArchive::new(file)?;
    let mut found = None;
    for index in 0..archive.len() {
        let mut member = archive.by_index(index)?;
        if Path::new(member.name())
            .file_name()
            .and_then(|value| value.to_str())
            != Some(name)
        {
            continue;
        }
        if found.is_some() {
            bail!("duplicate ZIP member filename: {name}");
        }
        let mut bytes = Vec::new();
        member.read_to_end(&mut bytes)?;
        found = Some(bytes);
    }
    found.with_context(|| format!("missing ZIP member {name}"))
}

fn decode_segment(data: &[u8], item: &EvSegment) -> Result<Vec<u8>> {
    if data.is_empty() || data.len() % 16 != 0 {
        bail!("{} is incomplete", item.file);
    }
    if hex::encode(Sha256::digest(data)) != item.encrypted_sha256.to_lowercase() {
        bail!("{} fingerprint mismatch", item.file);
    }
    let key = hex::decode(&item.key_hex)?;
    let mask = hex::decode(&item.xor_mask_hex)?;
    if key.len() != 32 || mask.len() != 16 {
        bail!("{} has invalid key material", item.file);
    }
    let mut buffer = data.to_vec();
    for (i, byte) in buffer.iter_mut().enumerate() {
        *byte ^= mask[i % 16];
    }
    ecb::Decryptor::<Aes256>::new_from_slice(&key)?
        .decrypt_padded_mut::<NoPadding>(&mut buffer)
        .map_err(|_| anyhow::anyhow!("{} has invalid AES block data", item.file))?;
    let padding = buffer.len() % 188;
    if padding > 15 || (padding > 0 && buffer[buffer.len() - padding..] != vec![b'#'; padding]) {
        bail!("{} has invalid padding", item.file);
    }
    if padding > 0 {
        buffer.truncate(buffer.len() - padding);
    }
    if buffer.is_empty()
        || buffer.len() % 188 != 0
        || buffer.iter().step_by(188).any(|byte| *byte != 0x47)
    {
        bail!("{} is not aligned MPEG-TS", item.file);
    }
    if (0..buffer.len())
        .step_by(188)
        .any(|i| ((buffer[i + 3] >> 4) & 3) == 0)
    {
        bail!("{} has an invalid TS packet", item.file);
    }
    Ok(buffer)
}

fn decode_ev(input: &Path, manifest: EvManifest, output: &Path) -> Result<()> {
    if !manifest.tool.contains("EVPlayer2 5.0.5")
        && manifest.variant != "xor16_then_aes256ecb_hash_padding"
    {
        bail!("manifest is not from a supported EVPlayer2 5.0.5 collector");
    }
    let mut items = manifest.segments;
    items.sort_by_key(|item| item.index);
    if items.is_empty()
        || items
            .iter()
            .enumerate()
            .any(|(index, item)| item.index != index as u32)
    {
        bail!("manifest indexes are incomplete");
    }
    if output.exists() {
        bail!("output already exists: {}", output.display());
    }
    if let Some(parent) = output.parent() {
        fs::create_dir_all(parent)?;
    }
    let partial = output.with_extension("partial");
    let mut destination = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&partial)?;
    for item in &items {
        destination.write_all(&decode_segment(
            &find_input_bytes(input, &item.file)?,
            item,
        )?)?;
    }
    destination.flush()?;
    fs::rename(partial, output)?;
    println!(
        "Decoded {} verified segments to {}",
        items.len(),
        output.display()
    );
    Ok(())
}

#[tokio::main]
async fn main() -> Result<()> {
    match Cli::parse().command {
        Command::Tree { catalog } => { let catalog: Catalog = read_json(&catalog)?; println!("{}", catalog.title); for root in &catalog.roots { print_tree(root, 1); } }
        Command::Download { manifest, output, parallel } => download_all(read_json(&manifest)?, output, parallel).await?,
        Command::DecodeEv { input, manifest, output } => decode_ev(&input, read_json(&manifest)?, &output)?,
        Command::CaptureEv { pid, input, output } => {
            #[cfg(windows)]
            evplayer_windows::capture(pid, &input, &output)?;
            #[cfg(not(windows))]
            bail!("capture-ev is only available in the Windows EVPlayer2 adapter");
        }
        Command::Adapters => println!("evplayer2-5.0.5/windows-live-manifest\ngeneric/http-segment-manifest\nandroid-agent-protocol (planned)\nmacos-agent-protocol (planned)\nios-companion-app-protocol (planned)"),
    }
    Ok(())
}
