//! One function per subcommand. `grab` and `capture-ev` are the only two that differ by
//! platform; everything else is portable.

#[cfg(not(windows))]
use anyhow::bail;
use anyhow::Result;
mod batch;
mod fetch;
mod recover;

use evmedia_contract::{
    CaptureEvArgs, CatalogArgs, Command, DownloadEvsArgs, ExportEvsArgs, ExportPhase, GrabArgs,
    Reporter,
};
use evmedia_core::{
    api, catalog, catalog_api::CatalogApi, decode, download, evs_manifest::Descriptor, harvest,
    playlist, read_json, remote_catalog,
};
use std::time::Duration;

const ADAPTERS: &str = "evplayer2-5.0.5/windows-live-manifest\ngeneric/http-segment-manifest\nandroid-agent-protocol (planned)\nmacos-agent-protocol (planned)\nios-companion-app-protocol (planned)";

pub async fn dispatch(command: Command, reporter: &Reporter) -> Result<()> {
    match command {
        Command::Tree(args) => catalog::run(&args.catalog, reporter),
        Command::Catalog(args) => catalog(args, reporter).await,
        Command::DownloadEvs(args) => download_evs(args, reporter).await,
        Command::ExportEvs(args) => export_evs(args, reporter).await,
        Command::ExportBatch(args) => batch::run(args, reporter).await,
        Command::Download(args) => {
            let manifest = download::load_input(&args.manifest)?;
            download::download_all(manifest, args.output, args.parallel, reporter).await
        }
        Command::DecodeEv(args) => {
            let manifest: decode::EvManifest = read_json(&args.manifest)?;
            decode::decode_ev(&args.input, manifest, &args.output, reporter)
        }
        Command::CaptureEv(args) => capture_ev(args, reporter),
        Command::Derive(args) => playlist::run(&args.playlist, &args.input, &args.output, reporter),
        Command::Fetch(args) => fetch::run(args, reporter).await,
        Command::ExportVideo(args) => {
            let text = std::fs::read_to_string(&args.playlist)?;
            let vod = evmedia_core::vod::Vod::parse(&text)?;
            let session = evmedia_core::full_export::Session::load(&args.session, &vod)?;
            let options = evmedia_core::full_export::Options {
                output: args.output,
                work: args.work,
                cache: args.cache,
                jobs: args.jobs,
                ffmpeg: args.ffmpeg,
                ffprobe: args.ffprobe,
            };
            evmedia_core::full_export::run(&text, &session, &options, reporter).await
        }
        Command::Grab(args) => grab(args, reporter),
        Command::Recover(args) => recover::run(args, reporter),
        Command::Adapters(_) => {
            reporter.info(ADAPTERS);
            Ok(())
        }
    }
}

async fn catalog(args: CatalogArgs, reporter: &Reporter) -> Result<()> {
    let session: evmedia_core::catalog_api::CatalogSession = read_json(&args.session)?;
    remote_catalog::fetch(
        &CatalogApi::new(session)?,
        args.account,
        &args.output,
        reporter,
    )
    .await
}

async fn download_evs(args: DownloadEvsArgs, reporter: &Reporter) -> Result<()> {
    let session: evmedia_core::catalog_api::CatalogSession = read_json(&args.session)?;
    let api = CatalogApi::new(session)?;
    let video = authorized_video(&api, args.account, args.course, args.file).await?;
    let signed = api.download_url(&video).await?;
    let url = signed["signed_url"]
        .as_str()
        .ok_or_else(|| anyhow::anyhow!("download response has no signed_url"))?;
    let bytes = api.get_url(url).await?;
    if bytes.is_empty() {
        anyhow::bail!("downloaded EVS file is empty");
    }
    if let Some(parent) = args.output.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(&args.output, &bytes)?;
    let key = api.download_key(args.file).await?;
    let tkey = key["tkey"]
        .as_str()
        .ok_or_else(|| anyhow::anyhow!("download key response has no tkey"))?;
    let descriptor = Descriptor::open(tkey)?;
    let filename = video["upload_key"].as_str().unwrap_or("video.evs");
    let manifest = descriptor.manifest(&bytes, filename)?;
    let m3u8 = args.output.with_extension("m3u8");
    std::fs::write(&m3u8, manifest)?;
    std::fs::write(
        args.output.with_extension("descriptor.json"),
        serde_json::to_vec_pretty(&descriptor)?,
    )?;
    reporter.info(format!(
        "EVS 下载完成：{}；清单：{}",
        args.output.display(),
        m3u8.display()
    ));
    Ok(())
}

/// Remove a file if it is there. A missing file is the normal case on a first run, so this is not an
/// error; anything else is.
pub(crate) fn remove_if_present(path: &std::path::Path) -> Result<()> {
    match std::fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(anyhow::anyhow!(
            "cannot clear the previous {}: {error}",
            path.display()
        )),
    }
}

pub(crate) async fn authorized_video(
    api: &CatalogApi,
    account: i64,
    course: i64,
    file: i64,
) -> Result<serde_json::Value> {
    let detail = api.course(account, course).await?;
    let mut todo = vec![detail["course_detail"].clone()];
    while let Some(node) = todo.pop() {
        todo.extend(node["childs"].as_array().cloned().unwrap_or_default());
        if let Some(found) = node["files"].as_array().and_then(|files| {
            files
                .iter()
                .find(|candidate| candidate["file_id"].as_i64() == Some(file))
        }) {
            return Ok(found.clone());
        }
    }
    anyhow::bail!("file is not in the authorized course")
}

/// Download one EVS file, ask the EVS descriptor's endpoint for every segment token, then use the
/// existing download/derive/decode path. This is the stable offline path: it never needs a player
/// process or a memory capture after the session JSON has been obtained.
async fn export_evs(args: ExportEvsArgs, reporter: &Reporter) -> Result<()> {
    match args.phase {
        ExportPhase::All => {
            download_export(&args, reporter).await?;
            convert_export(&args, reporter)
        }
        ExportPhase::Download => download_export(&args, reporter).await,
        ExportPhase::Convert => convert_export(&args, reporter),
        ExportPhase::Merge => merge_export(&args, reporter),
        ExportPhase::Publish => publish_export(&args, reporter),
    }
}

/// Authorize a lesson and cache every encrypted segment. This phase is I/O-bound, so the GUI can
/// run many lessons here without starting any CPU-heavy decode or encode work.
async fn download_export(args: &ExportEvsArgs, reporter: &Reporter) -> Result<()> {
    let session: evmedia_core::catalog_api::CatalogSession = read_json(&args.session)?;
    let api = CatalogApi::new(session)?;
    let video = authorized_video(&api, args.account, args.course, args.file).await?;
    let signed = api.download_url(&video).await?;
    let url = signed["signed_url"]
        .as_str()
        .ok_or_else(|| anyhow::anyhow!("download response has no signed_url"))?;
    let bytes = api.get_url(url).await?;
    if bytes.is_empty() {
        anyhow::bail!("downloaded EVS file is empty");
    }
    let key = api.download_key(args.file).await?;
    let tkey = key["tkey"]
        .as_str()
        .ok_or_else(|| anyhow::anyhow!("download key response has no tkey"))?;
    let descriptor = Descriptor::open(tkey)?;
    let filename = video["upload_key"].as_str().unwrap_or("video.evs");
    let m3u8_text = descriptor.manifest(&bytes, filename)?;
    let vod = evmedia_core::vod::Vod::parse(&m3u8_text)?;
    let liststr = vod
        .names
        .iter()
        .enumerate()
        .map(|(index, name)| format!("{index}|0|{name}"))
        .collect::<Vec<_>>()
        .join(",");
    let request = api::ListRequest::with_endpoint(&descriptor.req, &descriptor.cache_key, liststr);
    let list_value = api::fetch_list(&request, &api.session.token).await?;
    let list: playlist::Playlist = serde_json::from_value(list_value)?;
    playlist::summarize(&list)?;
    let actual = list
        .ordered()?
        .into_iter()
        .map(|(_, name)| name)
        .collect::<Vec<_>>();
    if actual != vod.names {
        anyhow::bail!("EVS signed segment list does not match the embedded complete M3U8");
    }

    std::fs::create_dir_all(&args.work)?;
    let evs_path = args.work.join(filename);
    let m3u8_path = args.work.join("original.m3u8");
    let list_path = args.work.join("list.json");
    std::fs::write(&evs_path, &bytes)?;
    std::fs::write(&m3u8_path, &m3u8_text)?;
    std::fs::write(&list_path, serde_json::to_vec_pretty(&list)?)?;
    let enc_dir = args.work.join("enc");
    download::download_all(
        list.to_download_manifest()?,
        enc_dir.clone(),
        args.jobs,
        reporter,
    )
    .await?;
    reporter.info(format!(
        "视频下载完成：{}（{} 个分段，等待转换）",
        args.work.display(),
        vod.names.len()
    ));
    Ok(())
}

/// Convert one cached lesson for callers that still want the old two-stage CLI contract.
fn convert_export(args: &ExportEvsArgs, reporter: &Reporter) -> Result<()> {
    merge_export(args, reporter)?;
    publish_export(args, reporter)
}

/// Derive and decrypt only the files cached by `download_export`. It makes no network request and
/// writes the per-lesson ordered intermediate `lesson.ts`, so a batch can run this independently
/// of downloads and output publishing.
fn merge_export(args: &ExportEvsArgs, reporter: &Reporter) -> Result<()> {
    let m3u8_path = args.work.join("original.m3u8");
    let list_path = args.work.join("list.json");
    let m3u8_text = std::fs::read_to_string(&m3u8_path).map_err(|error| {
        anyhow::anyhow!("读取已下载的视频描述 {} 失败：{error}", m3u8_path.display())
    })?;
    let vod = evmedia_core::vod::Vod::parse(&m3u8_text)?;
    let enc_dir = args.work.join("enc");
    let manifest_path = args.work.join("manifest.json");
    playlist::run(&list_path, &enc_dir, &manifest_path, reporter)?;
    let ev_manifest: decode::EvManifest = read_json(&manifest_path)?;
    let merged = args.work.join("lesson.ts");
    // `decode-ev` refuses to overwrite its output, which is right for a command whose output is the
    // deliverable. Here the merge is an intermediate in the lesson's own `--work` directory, and a
    // rerun regenerates it from segments that are already cached — so a leftover one is cleared
    // rather than allowed to fail the rerun. This is what makes "delete the outputs and export
    // again" work, and it is a rerun of the same lesson by construction: `--work` is per lesson.
    remove_if_present(&merged)?;
    // The global-queue build used `lesson.partial` while assembling a lesson. A subsequent
    // per-video conversion must clear that equally disposable intermediate before `decode-ev`
    // opens it with `create_new`; otherwise an interrupted previous run becomes a false failure.
    remove_if_present(&merged.with_extension("partial"))?;
    decode::decode_ev_parallel(&enc_dir, ev_manifest, &merged, args.jobs, reporter)?;

    reporter.info(format!(
        "视频解密和有序合并完成：{}（{} 个分段，等待封装）",
        args.work.display(),
        vod.names.len()
    ));
    Ok(())
}

/// Validate and publish the `lesson.ts` that `merge_export` has already produced. This is kept as
/// its own phase because ffmpeg/remux work must not hold a download or decrypt worker slot.
fn publish_export(args: &ExportEvsArgs, reporter: &Reporter) -> Result<()> {
    let m3u8_path = args.work.join("original.m3u8");
    let m3u8_text = std::fs::read_to_string(&m3u8_path).map_err(|error| {
        anyhow::anyhow!("读取已下载的视频描述 {} 失败：{error}", m3u8_path.display())
    })?;
    let vod = evmedia_core::vod::Vod::parse(&m3u8_text)?;
    let merged = args.work.join("lesson.ts");
    if !merged.is_file() {
        anyhow::bail!(
            "找不到已合并的视频 {}；请先运行 --phase merge",
            merged.display()
        );
    }
    let extension = args
        .output
        .extension()
        .and_then(|x| x.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if extension != "mp4" && extension != "mkv" {
        anyhow::bail!("output extension must be .mp4 or .mkv");
    }
    // Refusing to overwrite is the default because a batch that silently re-encoded everything
    // would be worse than one that stops; `--force` is how a caller says it meant it.
    if args.output.exists() && !args.force {
        anyhow::bail!(
            "output already exists: {} (pass --force to overwrite)",
            args.output.display()
        );
    }
    let options = evmedia_core::full_export::Options {
        output: args.output.clone(),
        work: args.work.clone(),
        cache: None,
        jobs: args.jobs,
        ffmpeg: args.ffmpeg.clone(),
        ffprobe: args.ffprobe.clone(),
    };
    evmedia_core::verified_media::publish(&merged, &vod, &options, &args.work, reporter)?;
    std::fs::copy(
        args.work.join(format!("report-{extension}.json")),
        args.work.join("report.json"),
    )?;
    reporter.info(format!(
        "EVS 导出完成：{}（{} 个分段）",
        args.output.display(),
        vod.names.len()
    ));
    Ok(())
}

#[cfg(windows)]
fn capture_ev(args: CaptureEvArgs, reporter: &Reporter) -> Result<()> {
    evmedia_win::capture::capture(args.pid, &args.input, &args.output, reporter)
}

#[cfg(not(windows))]
fn capture_ev(args: CaptureEvArgs, reporter: &Reporter) -> Result<()> {
    let _ = (args, reporter);
    bail!("capture-ev is only available in the Windows EVPlayer2 adapter");
}

#[cfg(windows)]
fn grab(args: GrabArgs, reporter: &Reporter) -> Result<()> {
    let source = evmedia_win::WinSource::open(args.pid)?;
    let options = harvest::GrabOptions {
        output: args.output,
        jobs: args.jobs,
        poll: Duration::from_secs(args.poll),
        idle_limit: args.idle_limit,
        attempts: args.attempts,
        mp4: args.mp4,
        sweep: !args.no_sweep,
        press_gap: Duration::from_millis(args.sweep_gap_ms),
    };
    harvest::grab::run(&source, &options, reporter)
}

#[cfg(not(windows))]
fn grab(args: GrabArgs, reporter: &Reporter) -> Result<()> {
    let _ = (args, reporter);
    bail!("grab is only available in the Windows EVPlayer2 adapter");
}
