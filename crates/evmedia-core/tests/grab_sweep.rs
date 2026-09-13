//! The sweep: what the loop does when the lesson has a gap behind the playhead.
//!
//! A segment the playhead ran past was never decrypted, so its key was never computed and no
//! amount of waiting produces it. The only cure is to move the playhead back onto it — which
//! means the loop has to *notice* the gap, and the gap is invisible to anything that only looks
//! at segments it already holds keys for. That is the mistake these tests exist to catch.

mod harvest_fixture;

use evmedia_contract::Reporter;
use evmedia_core::harvest::{grab, MAX_SWEEPS};
use harvest_fixture::{options, scratch, segment_key, stage};
use std::time::Duration;

/// The gap the sweep exists for: a segment with a context and a URL but no key. Moving the
/// playhead onto it is the only thing that can produce one.
#[test]
fn a_gap_behind_the_playhead_makes_the_loop_seek_back_to_it() {
    let output = scratch("sweep");
    let (fixture, plains) = stage(&output, 6);
    let file = fixture.forget_key(3);
    fixture.unlock_on_seek(vec![(3, (file, segment_key(3)))]);
    fixture.set_seek_reply(true);

    let mut opts = options(&output);
    opts.sweep = true;
    opts.poll = Duration::from_millis(1);
    opts.idle_limit = 20;
    grab::run(&fixture, &opts, &Reporter::silent()).unwrap();

    assert_eq!(fixture.seeks(), vec![3], "the sweep must aim at the earliest missing index");
    assert_eq!(
        std::fs::read(output.join("lesson.ts")).expect("lesson.ts"),
        plains.concat(),
        "the segment the playhead skipped must end up in the merge"
    );
}

/// A source whose seek keys are unbound says so, and the loop must believe it the first time
/// rather than hammering the player for the rest of the run.
#[test]
fn a_source_that_cannot_seek_is_asked_once_and_then_left_alone() {
    let output = scratch("nosweep");
    let (fixture, _) = stage(&output, 6);
    fixture.forget_key(3);
    fixture.set_seek_reply(false);

    let mut opts = options(&output);
    opts.sweep = true;
    opts.poll = Duration::from_millis(1);
    opts.idle_limit = 40;
    grab::run(&fixture, &opts, &Reporter::silent()).unwrap();

    assert_eq!(fixture.seeks(), vec![3], "an unbound seek must not be retried");
    assert!(output.join("lesson.partial.ts").exists());
}

/// The other half of that contract: a playhead that answers yes and changes nothing must still
/// terminate. Without the attempt cap this is an infinite loop, and it is the failure mode a
/// seek that silently does nothing would produce.
#[test]
fn a_sweep_that_never_helps_gives_up_rather_than_looping_forever() {
    let output = scratch("sweepburn");
    let (fixture, _) = stage(&output, 6);
    fixture.forget_key(3);
    fixture.set_seek_reply(true);

    let mut opts = options(&output);
    opts.sweep = true;
    opts.poll = Duration::from_millis(1);
    opts.idle_limit = 50;
    let started = std::time::Instant::now();
    grab::run(&fixture, &opts, &Reporter::silent()).unwrap();

    assert!(started.elapsed() < Duration::from_secs(10), "the sweep must be bounded");
    assert_eq!(fixture.seeks().len(), MAX_SWEEPS, "and it must stop at the cap, not before it");
}
