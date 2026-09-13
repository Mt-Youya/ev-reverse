//! The harvest loop.
//!
//! Shape of a run: poll the player for (index, file, key) and for signed URLs, join the two on
//! filename, download and decrypt whatever is new, and repeat until the lesson stops growing.
//! Everything already decrypted is on disk, so a rerun resumes.
//!
//! The loop body is a direct port of the version that completed a 43-minute lesson end to end.
//! The borrow structure in the worker pool in particular is deliberately left as it was — it is
//! the part most easily broken by a well-meaning rewrite.

use super::{fetch, GrabOptions, Harvester, Segment, MAX_SWEEPS, SWEEP_AFTER};
use crate::media;
use anyhow::Result;
use evmedia_contract::{Event, Reporter, SegmentState, Stage, StageState, Status};
use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    fs,
    time::{Duration, Instant},
};

pub fn run(harvester: &dyn Harvester, options: &GrabOptions, reporter: &Reporter) -> Result<()> {
    let enc_dir = options.output.join("enc");
    let dec_dir = options.output.join("dec");
    fs::create_dir_all(&enc_dir)?;
    fs::create_dir_all(&dec_dir)?;

    reporter.info(format!(
        "attached to EVPlayer2 pid={} (memory scan only, no injection)",
        harvester.pid()
    ));

    let client = reqwest::blocking::Client::builder()
        .user_agent("restclient-cpp")
        .timeout(Duration::from_secs(60))
        .build()?;

    let started = Instant::now();
    // Resume from anything already decrypted: the merge and the completeness check both work
    // off `ok`, so pre-existing files have to be counted or a rerun would throw them away.
    let mut ok: BTreeMap<u32, String> = fetch::resume(&dec_dir);
    if !ok.is_empty() {
        reporter.info(format!("resuming with {} already decrypted segment(s)", ok.len()));
    }

    let mut failed: HashMap<u32, (usize, String)> = HashMap::new();
    let mut idle = 0usize;
    let mut last_report = Instant::now();
    // Every segment index the player has ever exposed. The live window slides, so without this
    // a gap would be invisible the moment the playhead moved on -- and the sweep would have
    // nothing to aim at.
    let mut seen: BTreeSet<u32> = BTreeSet::new();
    let mut sweeps_left = MAX_SWEEPS;
    let mut cancelled = false;

    reporter.event(&Event::Stage {
        name: Stage::Scan,
        state: StageState::Begin,
        detail: String::new(),
    });

    loop {
        if reporter.stopped() {
            reporter.info("stop requested; finishing");
            cancelled = true;
            break;
        }

        let keys = harvester.keys()?;
        let urls = harvester.urls();
        if keys.is_empty() && urls.is_empty() && ok.is_empty() && started.elapsed().as_secs() < 30 {
            reporter.info(format!("  nothing visible yet: {}", harvester.diagnose()));
        }

        let jobs: Vec<Segment> = keys
            .iter()
            .filter_map(|(index, (file, key))| {
                let url = urls.get(file)?;
                Some(Segment { index: *index, file: file.clone(), key: key.clone(), url: url.clone() })
            })
            .collect();

        let mut progressed = false;
        // Only indexes that joined a signed URL of this lesson count: the player can hold
        // contexts from more than one lesson, and a stray index would send the sweep chasing a
        // gap that does not belong to the lesson being captured.
        seen.extend(jobs.iter().map(|segment| segment.index));

        let pending: Vec<&Segment> = jobs
            .iter()
            .filter(|segment| {
                !ok.contains_key(&segment.index)
                    && !failed.get(&segment.index).is_some_and(|(n, _)| *n >= options.attempts)
            })
            .collect();

        if !pending.is_empty() {
            let mut results: Vec<(u32, String, Result<()>)> = Vec::with_capacity(pending.len());
            {
                let next = std::sync::atomic::AtomicUsize::new(0);
                let sink = std::sync::Mutex::new(&mut results);
                std::thread::scope(|scope| {
                    for _ in 0..options.jobs.max(1) {
                        scope.spawn(|| loop {
                            let index = next.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                            if index >= pending.len() {
                                break;
                            }
                            let segment = pending[index];
                            let outcome = fetch::fetch_and_decode(&client, &enc_dir, &dec_dir, segment);
                            sink.lock().unwrap().push((
                                segment.index,
                                segment.file.clone(),
                                outcome,
                            ));
                        });
                    }
                });
            }
            for (index, file, outcome) in results {
                match outcome {
                    Ok(()) => {
                        ok.insert(index, file.clone());
                        failed.remove(&index);
                        progressed = true;
                        reporter.event(&Event::Segment {
                            index,
                            file,
                            state: SegmentState::Done,
                            attempt: 1,
                            error: None,
                        });
                    }
                    Err(error) => {
                        let entry = failed.entry(index).or_insert((0, String::new()));
                        entry.0 += 1;
                        entry.1 = error.to_string();
                        reporter.event(&Event::Segment {
                            index,
                            file,
                            state: SegmentState::Failed,
                            attempt: entry.0,
                            error: Some(entry.1.clone()),
                        });
                    }
                }
            }
        }
        for segment in &jobs {
            if !ok.contains_key(&segment.index) && fetch::valid_dec_file(&dec_dir, segment.index) {
                ok.insert(segment.index, segment.file.clone());
            }
        }

        if progressed {
            idle = 0;
        } else {
            idle += 1;
        }

        // The GUI gets a sample every poll; a terminal user still sees the counters only every
        // ten seconds, so the cadence they are used to does not change.
        reporter.event(&Event::Progress {
            stage: Stage::Scan,
            keys: keys.len(),
            urls: urls.len(),
            segments: jobs.len(),
            done: ok.len(),
            failed: failed.len(),
            elapsed_secs: started.elapsed().as_secs(),
        });
        if last_report.elapsed() > Duration::from_secs(10) {
            last_report = Instant::now();
            reporter.info(format!(
                "  keys={:<5} urls={:<5} segments={:<5} done={:<5} failed={:<4} elapsed={}s",
                keys.len(),
                urls.len(),
                jobs.len(),
                ok.len(),
                failed.len(),
                started.elapsed().as_secs()
            ));
        }

        // The visible segment set grows as the lesson plays, so "everything known is done" is
        // not the same as "the lesson is complete"; keep polling and let the idle limit or the
        // stop signal end the run.
        let known: Vec<u32> = jobs.iter().map(|segment| segment.index).collect();
        if !known.is_empty() && known.iter().all(|index| ok.contains_key(index)) && idle == 0 {
            reporter.info("  caught up with the player; keep playing to reveal more segments");
        }

        // Waiting cannot fill a gap the playhead already ran past -- those keys were never
        // computed. Walk the playhead back to the earliest one and let it decrypt again.
        let gap = seen.iter().find(|index| !ok.contains_key(index)).copied();
        if options.sweep && sweeps_left > 0 && idle >= SWEEP_AFTER {
            if let Some(target) = gap {
                reporter.info(format!(
                    "  stalled {idle} poll(s) with a gap behind the playhead; sweeping back to index {target}"
                ));
                sweeps_left -= 1;
                match harvester.seek_to(target, options.press_gap, reporter) {
                    Ok(true) => {
                        reporter.info("  playhead is inside the gap; letting the player re-decrypt");
                        idle = 0;
                        reporter.sleep(options.poll);
                        continue;
                    }
                    Ok(false) => {
                        // Seek keys are not bound in this build: stop trying, stay a passive
                        // observer rather than hammering the player.
                        reporter.info("  sweep unavailable; waiting for manual playback instead");
                        sweeps_left = 0;
                    }
                    Err(error) => reporter.info(format!("  sweep failed: {error}")),
                }
            }
        }

        if idle >= options.idle_limit {
            reporter.info(format!("no progress for {idle} polls; stopping"));
            break;
        }
        reporter.sleep(options.poll);
    }

    reporter.event(&Event::Stage {
        name: Stage::Scan,
        state: StageState::End,
        detail: String::new(),
    });

    finish(&dec_dir, options, &ok, cancelled, reporter)
}

fn finish(
    dec_dir: &std::path::Path,
    options: &GrabOptions,
    ok: &BTreeMap<u32, String>,
    cancelled: bool,
    reporter: &Reporter,
) -> Result<()> {
    if ok.is_empty() {
        reporter.info("nothing decrypted yet; keep the lesson playing and rerun");
        reporter.event(&Event::Finished {
            status: Status::Nothing,
            exit_code: evmedia_contract::exit::OK,
            message: String::new(),
        });
        return Ok(());
    }

    let indexes: Vec<u32> = ok.keys().copied().collect();
    let outcome = media::merge_lesson(dec_dir, &options.output, &indexes, reporter)?;

    let status = if cancelled {
        Status::Cancelled
    } else if outcome.complete {
        Status::Complete
    } else {
        Status::Partial
    };

    if options.mp4 && outcome.complete {
        reporter.event(&Event::Stage {
            name: Stage::Remux,
            state: StageState::Begin,
            detail: String::new(),
        });
        let mp4 = options.output.join("lesson.mp4");
        media::remux_mp4(&outcome.path, &mp4, reporter)?;
        reporter.event(&Event::Stage {
            name: Stage::Remux,
            state: StageState::End,
            detail: String::new(),
        });
    } else if options.mp4 {
        reporter.info(
            "refusing to remux an incomplete merge; play the rest and rerun (segments are cached)",
        );
    }

    reporter.event(&Event::Finished {
        status,
        exit_code: evmedia_contract::exit::OK,
        message: format!("merged {} segment(s)", outcome.count),
    });
    Ok(())
}
