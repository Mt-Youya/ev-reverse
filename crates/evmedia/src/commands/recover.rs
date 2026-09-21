//! Windows-only recovery from an existing EVPlayer2 process.

use anyhow::Result;
use evmedia_contract::{Event, RecoverArgs, Reporter, Stage, StageState};

#[cfg(windows)]
pub(crate) fn run(args: RecoverArgs, reporter: &Reporter) -> Result<()> {
    use evmedia_core::{keyscan, keyscan::KeyEntry, media};
    use std::time::Duration;

    let Some(pid) = args.pid.or_else(evmedia_win::find_player_pid) else {
        anyhow::bail!("EVPlayer2 is not running; start it, play the lesson, then rerun");
    };
    let source = evmedia_win::WinSource::open(pid)?;
    let player = source.player();

    let library_path = args.output.join("keys.json");
    let mut library = keyscan::load_library(&library_path);
    reporter.info(format!("pid {pid}; {} key(s) already known", library.len()));

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

    let groups = keyscan::group_by_lesson(&library);
    for (lesson, group) in &groups {
        let lesson_dir = if groups.len() == 1 {
            args.output.clone()
        } else {
            args.output.join(format!("lesson-{lesson}"))
        };
        let dec_dir = lesson_dir.join("dec");
        let written = keyscan::decrypt_into(&args.cache, &dec_dir, &group.entries)?;
        reporter.info(format!("lesson {lesson}: decrypted {written}/{} segment(s)", group.entries.len()));
        if !group.placed {
            reporter.info(format!(
                "  lesson {lesson} is not merged: its segments have no index the player can still \
                 name (their URLs are long gone), so their order is unknown. The decrypted files \
                 are in {}",
                dec_dir.display()
            ));
            continue;
        }
        let indexes: Vec<u32> = group.entries.keys().copied().collect();
        let merged = media::merge_lesson(&dec_dir, &lesson_dir, &indexes, reporter)?;
        if args.mp4 {
            media::remux_mp4(&merged.path, &lesson_dir.join("lesson.mp4"), reporter)?;
        }
    }
    Ok(())
}

#[cfg(not(windows))]
pub(crate) fn run(args: RecoverArgs, reporter: &Reporter) -> Result<()> {
    let _ = (args, reporter);
    anyhow::bail!("recover is only available in the Windows EVPlayer2 adapter");
}
