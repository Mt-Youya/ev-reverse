//! The seek algorithm, driven by a fake playhead.
//!
//! This is the part of the sweep that decides whether a gap can be filled at all, and it used to
//! live in the Windows crate where no test could reach it. What it needs from a player is exactly
//! two things — where the window is, and how to step — so a pair of numbers is enough.

use evmedia_contract::Reporter;
use evmedia_core::harvest::seek::{seek_window_to, Playhead};
use std::cell::{Cell, RefCell};
use std::time::Duration;

/// A playhead that is a pair of numbers.
struct FakePlayhead {
    window: Cell<Option<(u32, u32)>>,
    steps: RefCell<Vec<(bool, usize)>>,
    /// Segments moved per press. One is the simplest; other values exercise the calibration.
    per_press: i64,
    /// When set, stepping changes nothing — a player whose seek keys are unbound.
    stuck: bool,
}

impl FakePlayhead {
    fn new(low: u32, high: u32) -> Self {
        Self {
            window: Cell::new(Some((low, high))),
            steps: RefCell::new(Vec::new()),
            per_press: 1,
            stuck: false,
        }
    }

    fn at(&self) -> (u32, u32) {
        self.window.get().expect("the fake has a window")
    }

    fn steps(&self) -> Vec<(bool, usize)> {
        self.steps.borrow().clone()
    }
}

impl Playhead for FakePlayhead {
    fn window(&self) -> Option<(u32, u32)> {
        self.window.get()
    }

    fn step(&self, back: bool, count: usize, _gap: Duration) {
        self.steps.borrow_mut().push((back, count));
        if self.stuck {
            return;
        }
        let shift = self.per_press * count as i64 * if back { -1 } else { 1 };
        let Some((low, high)) = self.window.get() else { return };
        let moved = |value: u32| (value as i64 + shift).max(0) as u32;
        self.window.set(Some((moved(low), moved(high))));
    }
}

fn gap() -> Duration {
    Duration::from_millis(1)
}

fn seek(playhead: &FakePlayhead, target: u32) -> bool {
    seek_window_to(playhead, target, gap(), &Reporter::silent()).expect("the seek never fails")
}

#[test]
fn a_target_already_inside_the_window_is_not_sought() {
    let playhead = FakePlayhead::new(10, 14);
    assert!(seek(&playhead, 12));
    assert!(playhead.steps().is_empty(), "arriving already, it must not post a single key");
}

#[test]
fn a_target_ahead_of_the_window_is_walked_forward_to() {
    let playhead = FakePlayhead::new(10, 14);
    assert!(seek(&playhead, 30));
    assert_eq!(playhead.steps(), vec![(false, 16)], "the distance is 16 and one press moves 1");
    assert!(playhead.at().0 <= 30 && 30 <= playhead.at().1);
}

#[test]
fn a_target_behind_the_window_is_walked_back_to() {
    let playhead = FakePlayhead::new(100, 110);
    assert!(seek(&playhead, 20));
    assert_eq!(playhead.steps(), vec![(true, 80)]);
    assert!(playhead.at().0 <= 20 && 20 <= playhead.at().1);
}

#[test]
fn a_far_target_converges_over_several_rounds() {
    let playhead = FakePlayhead::new(0, 4);
    assert!(seek(&playhead, 1000));
    assert!(playhead.steps().len() > 1, "a target beyond one batch has to take more than one");
    assert!(playhead.at().0 <= 1000 && 1000 <= playhead.at().1);
}

/// A player that moves further per press than it was measured at has to be walked back, not
/// overshot again — this is the part of the loop that corrects its own estimate.
#[test]
fn an_overshoot_is_corrected_rather_than_repeated() {
    let playhead = FakePlayhead { per_press: 5, ..FakePlayhead::new(0, 4) };
    assert!(seek(&playhead, 100));
    assert_eq!(playhead.steps().len(), 2, "one to overshoot, one to come back");
    assert!(playhead.at().0 <= 100 && 100 <= playhead.at().1);
}

/// The contract the caller depends on: a seek that cannot move the playhead says so, once,
/// instead of firing keys forever.
#[test]
fn seek_keys_that_do_nothing_are_reported_rather_than_retried() {
    let playhead = FakePlayhead { stuck: true, ..FakePlayhead::new(10, 14) };
    assert!(!seek(&playhead, 30));
    assert_eq!(playhead.steps().len(), 1, "an unbound seek must be tried once, not repeatedly");
}

#[test]
fn a_player_holding_nothing_cannot_be_sought() {
    let playhead = FakePlayhead { window: Cell::new(None), ..FakePlayhead::new(0, 0) };
    assert!(!seek(&playhead, 30));
    assert!(playhead.steps().is_empty());
}
