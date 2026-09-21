use super::{authorized_video, remove_if_present};
use anyhow::Result;
use evmedia_contract::{Event, ExportBatchArgs, ExportBatchPlan, Reporter, Stage, StageState};
use evmedia_core::{api, catalog_api::CatalogApi, crypto, decode, evs_manifest::Descriptor, playlist, read_json, segment_queue};
use std::{collections::BTreeMap, io::Write, path::PathBuf, sync::mpsc, thread};

struct Lesson { output: PathBuf, work: PathBuf, vod: evmedia_core::vod::Vod, total: usize, next: u32, received: usize, pending: BTreeMap<u32, Vec<u8>>, partial: std::fs::File }
struct ReadyLesson { id: String, output: PathBuf, work: PathBuf, vod: evmedia_core::vod::Vod }

/// Flatten all videos' segments before downloading. A completion writes only its own lesson's next
/// contiguous bytes, so download/decrypt completion order never corrupts container order.
pub async fn run(args: ExportBatchArgs, reporter: &Reporter) -> Result<()> {
    let plan: ExportBatchPlan = read_json(&args.plan)?;
    if plan.videos.is_empty() { anyhow::bail!("batch plan contains no videos"); }
    let session = read_json(&args.session)?;
    let api = CatalogApi::new(session)?;
    let mut tasks = Vec::new(); let mut lessons = BTreeMap::new();
    // Publishing is deliberately serial: FFmpeg/NVENC is the expensive shared resource. Its
    // worker receives a lesson as soon as that lesson's ordered merge closes; it never waits for
    // unrelated downloads still in the global segment queue.
    let (publish_tx, publish_rx) = mpsc::channel::<ReadyLesson>();
    let publish_reporter = reporter.clone(); let publish_ffmpeg = args.ffmpeg.clone(); let publish_ffprobe = args.ffprobe.clone();
    let publisher = thread::spawn(move || -> Result<()> { for lesson in publish_rx {
        let merged = lesson.work.join("lesson.ts");
        let options = evmedia_core::full_export::Options { output: lesson.output, work: lesson.work.clone(), cache: None, jobs: 1, ffmpeg: publish_ffmpeg.clone(), ffprobe: publish_ffprobe.clone() };
        publish_reporter.info(format!("{}: all segments arrived; validating and publishing", lesson.id));
        evmedia_core::verified_media::publish(&merged, &lesson.vod, &options, &lesson.work, &publish_reporter)?;
    } Ok(()) });
    reporter.event(&Event::Stage { name: Stage::Scan, state: StageState::Begin, detail: format!("preparing {} video(s)", plan.videos.len()) });
    for p in plan.videos {
        // A batch must not turn one already-published lesson into a failure for every other
        // lesson. This matches the GUI's historical per-row behaviour and keeps reruns cheap.
        if p.output.exists() && !args.force { reporter.info(format!("{}: output already exists; skipped", p.id)); continue; }
        let video = authorized_video(&api, args.account, p.course, p.file).await?;
        let signed = api.download_url(&video).await?;
        let url = signed["signed_url"].as_str().ok_or_else(|| anyhow::anyhow!("download response has no signed_url"))?;
        let evs = api.get_url(url).await?;
        let key = api.download_key(p.file).await?;
        let descriptor = Descriptor::open(key["tkey"].as_str().ok_or_else(|| anyhow::anyhow!("download key response has no tkey"))?)?;
        let evs_name = video["upload_key"].as_str().unwrap_or("video.evs");
        let m3u8 = descriptor.manifest(&evs, evs_name)?; let vod = evmedia_core::vod::Vod::parse(&m3u8)?;
        let liststr = vod.names.iter().enumerate().map(|(i, n)| format!("{i}|0|{n}")).collect::<Vec<_>>().join(",");
        let list: playlist::Playlist = serde_json::from_value(api::fetch_list(&api::ListRequest::with_endpoint(&descriptor.req, &descriptor.cache_key, liststr), &api.session.token).await?)?;
        playlist::summarize(&list)?;
        if list.ordered()?.into_iter().map(|(_, n)| n).collect::<Vec<_>>() != vod.names { anyhow::bail!("EVS signed segment list does not match the embedded complete M3U8"); }
        std::fs::create_dir_all(&p.work)?; std::fs::write(p.work.join(evs_name), &evs)?; std::fs::write(p.work.join("original.m3u8"), &m3u8)?; std::fs::write(p.work.join("list.json"), serde_json::to_vec_pretty(&list)?)?;
        let partial_path = p.work.join("lesson.partial"); remove_if_present(&partial_path)?;
        let partial = std::fs::OpenOptions::new().write(true).create_new(true).open(partial_path)?;
        let owner = p.id.clone(); let host = list.d_p.clone(); let total = list.k_l.len();
        for entry in list.k_l {
            let name = entry.filename()?.to_string(); let key = crypto::key_from_tk(&entry.tk, &name)?;
            tasks.push(segment_queue::SegmentTask { lesson: owner.clone(), target: p.work.join("enc").join(&name), remote: evmedia_core::download::RemoteSegment { index: entry.idx, url: entry.url(&host), headers: BTreeMap::new(), sha256: None, filename: Some(name.clone()) }, manifest: decode::EvSegment { index: entry.idx, file: name.clone(), key_hex: crypto::hex_lower(key.as_bytes()), xor_mask_hex: crypto::hex_lower(&crypto::mask_from_filename(&name)), encrypted_sha256: String::new() } });
        }
        lessons.insert(owner, Lesson { output: p.output, work: p.work, vod, total, next: 0, received: 0, pending: BTreeMap::new(), partial });
    }
    let total = tasks.len(); let mut done = 0;
    reporter.event(&Event::Stage { name: Stage::Scan, state: StageState::End, detail: format!("{total} segment(s) ready") });
    reporter.event(&Event::Stage { name: Stage::Download, state: StageState::Begin, detail: format!("0/0 segment(s) of {total}") });
    let queued = segment_queue::run(tasks, args.download_jobs, args.decrypt_jobs, |segment| {
        let lesson = lessons.get_mut(&segment.lesson).ok_or_else(|| anyhow::anyhow!("unknown lesson {}", segment.lesson))?;
        lesson.received += 1; lesson.pending.insert(segment.index, segment.bytes);
        while let Some(bytes) = lesson.pending.remove(&lesson.next) { lesson.partial.write_all(&bytes)?; lesson.next += 1; }
        done += 1;
        if lesson.received == lesson.total {
            if !lesson.pending.is_empty() || lesson.next as usize != lesson.total { anyhow::bail!("{}: decrypted segment order has a gap", segment.lesson); }
            // Move this lesson into the publish queue immediately. It no longer occupies a merge
            // buffer while the remaining lessons continue downloading.
            let mut lesson = lessons.remove(&segment.lesson).expect("lesson was just found");
            lesson.partial.flush()?; drop(lesson.partial);
            let merged = lesson.work.join("lesson.ts"); remove_if_present(&merged)?; std::fs::rename(lesson.work.join("lesson.partial"), &merged)?;
            publish_tx.send(ReadyLesson { id: segment.lesson, output: lesson.output, work: lesson.work, vod: lesson.vod }).map_err(|_| anyhow::anyhow!("publish worker stopped"))?;
        }
        Ok(())
    }).await;
    drop(publish_tx);
    let published = publisher.join().map_err(|_| anyhow::anyhow!("publish worker panicked"))?;
    queued?; published?;
    reporter.event(&Event::Stage { name: Stage::Download, state: StageState::End, detail: format!("{done}/{done} segment(s) of {total}") });
    if !lessons.is_empty() { anyhow::bail!("global segment queue ended before every lesson was complete"); }
    Ok(())
}
