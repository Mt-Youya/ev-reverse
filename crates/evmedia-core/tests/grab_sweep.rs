//! The sweep: what the loop does when the lesson has a gap behind the playhead.
//!
//! A segment the playhead ran past was never decrypted, so its key was never computed and no
//! amount of waiting produces it. The only cure is to move the playhead back onto it — which means
//! the loop has to *notice* the gap, and the gap is invisible to anything that only looks at
//! segments it already holds keys for.
//!
//! These tests assert two things: where the playhead ended up, and how many presses it took to get
//! there. They no longer assert which index a seek was asked for, which is the part that had to
//! change whenever the stepping strategy did. The press count is still pinned — the fixture moves
//! exactly one index per press, so the arithmetic is checkable by hand — and that does mean a
//! change of batching will have to update these numbers rather than slide past them.

mod harvest_fixture;

use evmedia_contract::Reporter;
use evmedia_core::harvest::grab;
use harvest_fixture::{options, scratch, segment_key, stage};
use std::time::Duration;

/// The gap the sweep exists for: a segment with a context and a URL but no key. Moving the
/// playhead onto it is the only thing that can produce one.
#[test]
fn a_gap_behind_the_playhead_makes_the_loop_move_the_playhead_onto_it() {
    let output = scratch("sweep");
    let (fixture, plains) = stage(&output, 6);
    let file = fixture.forget_key(3);
    // The playhead sits at the start of the lesson and the gap is at index 3, so covering it
    // takes three presses of one segment each.
    fixture.unlock_on_step(vec![(3, (file, segment_key(3)))]);

    let mut opts = options(&output);
    opts.sweep = true;
    opts.poll = Duration::from_millis(1);
    opts.idle_limit = 20;
    grab::run(&fixture, &opts, &Reporter::silent()).unwrap();

    let (low, high) = fixture.window_at();
    assert!(low <= 3 && 3 <= high, "the playhead must end up covering the gap, not {low}..{high}");
    assert_eq!(fixture.steps(), vec![(false, 3)], "three segments forward, one press each");
    assert_eq!(
        std::fs::read(output.join("lesson.ts")).expect("lesson.ts"),
        plains.concat(),
        "the segment the playhead skipped must end up in the merge"
    );
}

/// A source whose seek keys are unbound says so by not moving, and the loop must believe it the
/// first time rather than hammering the player for the rest of the run.
#[test]
fn a_source_that_cannot_seek_is_asked_once_and_then_left_alone() {
    let output = scratch("nosweep");
    let (fixture, _) = stage(&output, 6);
    fixture.forget_key(3);
    fixture.set_stuck(true);

    let mut opts = options(&output);
    opts.sweep = true;
    opts.poll = Duration::from_millis(1);
    opts.idle_limit = 40;
    grab::run(&fixture, &opts, &Reporter::silent()).unwrap();

    assert_eq!(fixture.steps().len(), 1, "an unbound seek must be tried once, not repeatedly");
    assert_eq!(fixture.window_at(), (0, 0), "and the playhead must not have moved");
    assert!(output.join("lesson.partial.ts").exists());
}

/// The other half of that contract: a playhead that moves and a gap that still never fills must
/// still terminate. Without the attempt cap this is an infinite loop.
#[test]
fn a_sweep_that_never_helps_gives_up_rather_than_looping_forever() {
    let output = scratch("sweepburn");
    let (fixture, _) = stage(&output, 6);
    fixture.forget_key(3);
    // The playhead moves, but nothing is registered as arriving, so the key never appears — the
    // case where a seek can report success forever without the lesson getting any closer.

    let mut opts = options(&output);
    opts.sweep = true;
    opts.poll = Duration::from_millis(1);
    opts.idle_limit = 50;
    let started = std::time::Instant::now();
    grab::run(&fixture, &opts, &Reporter::silent()).unwrap();

    assert!(started.elapsed() < Duration::from_secs(10), "the sweep must be bounded");
    assert_eq!(fixture.steps().len(), 1, "and the playhead must not be walked over and over");
    assert!(
        output.join("lesson.partial.ts").exists(),
        "the gap is still a gap, and the merge must say so"
    );
}
