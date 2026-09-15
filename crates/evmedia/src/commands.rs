//! One function per subcommand. `grab` and `capture-ev` are the only two that differ by
//! platform; everything else is portable.

use anyhow::Result;
#[cfg(not(windows))]
use anyhow::bail;
use evmedia_contract::{
    CaptureEvArgs, Command, Event, FetchArgs, GrabArgs, RecoverArgs, Reporter, Stage, StageState,
};
use evmedia_core::{api, catalog, decode, download, harvest, keyscan, media, playlist, read_json};
use evmedia_core::keyscan::KeyEntry;
use std::time::Duration;

const ADAPTERS: &str = "evplayer2-5.0.5/windows-live-manifest\ngeneric/http-segment-manifest\nandroid-agent-protocol (planned)\nmacos-agent-protocol (planned)\nios-companion-app-protocol (planned)";

pub async fn dispatch(command: Command, reporter: &Reporter) -> Result<()> {
    match command {
        Command::Tree(args) => catalog::run(&args.catalog, reporter),
        Command::Download(args) => {
            let manifest: download::DownloadManifest = read_json(&args.manifest)?;
            download::download_all(manifest, args.output, args.parallel, reporter).await
        }
        Command::DecodeEv(args) => {
            let manifest: decode::EvManifest = read_json(&args.manifest)?;
            decode::decode_ev(&args.input, manifest, &args.output, reporter)
        }
        Command::CaptureEv(args) => capture_ev(args, reporter),
        Command::Derive(args) => {
            playlist::run(&args.playlist, &args.input, &args.output, reporter)
        }
        Command::Fetch(args) => fetch(args, reporter).await,
        Command::Grab(args) => grab(args, reporter),
        Command::Recover(args) => recover(args, reporter),
        Command::Adapters(_) => {
            reporter.info(ADAPTERS);
            Ok(())
        }
    }
}

/// `fetch`: the player's own request, made without the player.
///
/// The port is the same for every platform — the request is built, signed and encrypted in the
/// portable core — so unlike `grab` and `capture-ev` this one has no Windows half.
async fn fetch(args: FetchArgs, reporter: &Reporter) -> Result<()> {
    let (playkey, liststr) = if let Some(path) = &args.from_capture {
        let (playkey, liststr) = api::fields_from_capture(path)?;
        reporter.info(format!(
            "play key and {} segment name(s) read from {}",
            liststr.matches(',').count() + 1,
            path.display()
        ));
        (playkey, liststr)
    } else if let Some(path) = &args.from_body {
        let body = std::fs::read(path)
            .map_err(|error| anyhow::anyhow!("read {}: {error}", path.display()))?;
        let (playkey, liststr) = api::request_fields(&body)?;
        reporter.info(format!(
            "play key and {} segment name(s) read from {}",
            liststr.matches(',').count() + 1,
            path.display()
        ));
        (playkey, liststr)
    } else {
        if args.playkey.is_empty() || args.liststr.is_empty() {
            anyhow::bail!("give --from-body, or both --playkey and --liststr");
        }
        (args.playkey.clone(), args.liststr.clone())
    };

    let request = api::ListRequest::new(playkey, liststr);
    let req_time = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs())
        .unwrap_or(0);
    reporter.info(format!(
        "POST {}{} for {} segment(s); {} bytes of signed fields, sign {}",
        request.host,
        request.endpoint,
        request.segment_count(),
        request.sign_input(req_time).len(),
        request.sign(req_time)
    ));

    let list = api::fetch_list(&request, &args.token).await?;
    if let Some(parent) = args.output.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(&args.output, serde_json::to_vec_pretty(&list)?)?;
    let signed = list.get("k_l").and_then(|value| value.as_array()).map(Vec::len).unwrap_or(0);
    reporter.info(format!("{} segment(s) signed to {}", signed, args.output.display()));
    Ok(())
}

#[cfg(windows)]
fn capture_ev(args: CaptureEvArgs, reporter: &Reporter) -> Result<()> {    evmedia_win::capture::capture(args.pid, &args.input, &args.output, reporter)
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

#[cfg(windows)]
fn recover(args: RecoverArgs, reporter: &Reporter) -> Result<()> {
    let Some(pid) = args.pid.or_else(evmedia_win::find_player_pid) else {
        anyhow::bail!("EVPlayer2 is not running; start it, play the lesson, then rerun");
    };
    let source = evmedia_win::WinSource::open(pid)?;
    let player = source.player();

    // The library persists across runs. A sweep that is interrupted, or a player that restarts,
    // must not cost the keys already paid for -- and a segment already solved never needs testing
    // again, which is what keeps a resumed sweep cheap.
    let library_path = args.output.join("keys.json");
    let mut library = keyscan::load_library(&library_path);
    reporter.info(format!(
        "pid {pid}; {} key(s) already known",
        library.len()
    ));

    reporter.event(&Event::Stage {
        name: Stage::Scan,
        state: StageState::Begin,
        detail: if args.sweep { "sweeping the playhead" } else { "reading keys" }.to_string(),
    });

    if args.sweep {
        let options = evmedia_win::sweep::SweepOptions {
            batch: args.sweep_batch,
            gap: Duration::from_millis(args.sweep_gap_ms),
            idle_rounds: args.sweep_idle,
            ..Default::default()
        };
        let gained = evmedia_win::sweep::collect(player, &args.cache, &mut library, &options, reporter)?;
        reporter.info(format!("the sweep added {gained} key(s)"));
    } else {
        // Report the candidate count separately from the hit count. Without it, a scan that found
        // nothing to look at is indistinguishable from a scan that looked and found no key.
        let candidates = player.hex_candidates();
        reporter.info(format!("{} 32-hex candidate(s) in memory", candidates.len()));
        let found = player.recover_pairs(&args.cache, &candidates, &library);
        reporter.info(format!("{} key(s) live right now", found.len()));
        for (file, key) in found {
            library.entry(file).or_insert(KeyEntry { key, index: None, lesson: None });
        }
        player.fill_metadata(&mut library);
    }

    reporter.event(&Event::Stage {
        name: Stage::Scan,
        state: StageState::End,
        detail: format!("{} key(s)", library.len()),
    });
    keyscan::save_library(&library_path, &library)?;

    if library.is_empty() {
        reporter.info(
            "Nothing was recoverable. The player computes a segment's key only while it decrypts \
             that segment for playback, and it downloads without ever computing one -- so the \
             lesson has to be played, or swept with --sweep, before any key exists to read."
                .to_string(),
        );
        return Ok(());
    }

    // One output tree per lesson. This is not cosmetic: segment indexes restart at 0 for every
    // lesson, so decrypting and merging the whole library at once would overwrite one lesson's
    // segments with another's and produce a file that looks fine and is the wrong length.
    let groups = keyscan::group_by_lesson(&library);
    for (lesson, group) in &groups {
        let lesson_dir = if groups.len() == 1 {
            args.output.clone()
        } else {
            args.output.join(format!("lesson-{lesson}"))
        };
        let dec_dir = lesson_dir.join("dec");
        let written = keyscan::decrypt_into(&args.cache, &dec_dir, &group.entries)?;
        reporter.info(format!(
            "lesson {lesson}: decrypted {written}/{} segment(s)",
            group.entries.len()
        ));

        if !group.placed {
            reporter.info(format!(
                "  lesson {lesson} is not merged: its segments have no index the player can still \
                 name (their URLs are long gone), so their order is unknown. The decrypted files \
                 are in {}",
                dec_dir.display()
            ));
            continue;
        }

        // `entries` is a BTreeMap, so indexes arrive sorted — which the merge depends on, since it
        // concatenates in the order given and decides completeness from the highest index.
        let indexes: Vec<u32> = group.entries.keys().copied().collect();
        let merged = media::merge_lesson(&dec_dir, &lesson_dir, &indexes, reporter)?;
        if args.mp4 {
            media::remux_mp4(&merged.path, &lesson_dir.join("lesson.mp4"), reporter)?;
        }
    }
    Ok(())
}

#[cfg(not(windows))]
fn recover(args: RecoverArgs, reporter: &Reporter) -> Result<()> {
    let _ = (args, reporter);
    bail!("recover is only available in the Windows EVPlayer2 adapter");
}
