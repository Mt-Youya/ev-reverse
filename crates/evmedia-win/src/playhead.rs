//! Driving the player's playhead without touching its UI.
//!
//! A segment the playhead never reached has no key anywhere: the key appears only once the player
//! decrypts that segment for playback, and nothing in the download path computes it. So a gap left
//! behind the playhead can only be filled by putting the playhead back inside the gap and letting
//! the read-ahead decrypt it again.
//!
//! Posting `WM_KEYDOWN`/`WM_KEYUP` to the player's own window does that without focus, without
//! injection, and without knowing a single UI coordinate — only the window handle is needed.
//!
//! The algorithm itself — when to step, how far, and when to give up — is portable and lives in
//! `evmedia_core::harvest::seek`, where it is tested without a player. This module supplies the
//! only two things it cannot know: where the live key window is, and how to post a key.

use crate::process::Player;
use anyhow::Result;
use evmedia_contract::Reporter;
use evmedia_core::harvest::seek::{self, Playhead};
use std::time::Duration;
use windows_sys::Win32::{
    Foundation::RECT,
    UI::WindowsAndMessaging::{
        EnumWindows, GetWindowRect, GetWindowThreadProcessId, IsWindowVisible, PostMessageW,
        WM_KEYDOWN, WM_KEYUP,
    },
};

const VK_LEFT: usize = 0x25;
const VK_RIGHT: usize = 0x27;

/// The key that steps the playhead forward by one increment.
pub const STEP_FORWARD: usize = VK_RIGHT;

/// Post `count` presses of `vk` to the player's window, returning how many were sent.
///
/// Zero means the player has no visible window to post to, which is the caller's cue to stop
/// rather than loop. Used by the key sweep, which walks the playhead across a whole lesson so the
/// player decrypts every segment along the way.
pub fn press(player: &Player, vk: usize, count: usize, gap: Duration) -> usize {
    let Some(hwnd) = player_window(player.pid()) else {
        return 0;
    };
    post_key(hwnd, vk, count, gap);
    count
}

struct WindowPick {
    pid: u32,
    best: isize,
    area: i64,
}

unsafe extern "system" fn pick_window(hwnd: isize, lparam: isize) -> i32 {
    let pick = &mut *(lparam as *mut WindowPick);
    if IsWindowVisible(hwnd) == 0 {
        return 1;
    }
    let mut owner = 0u32;
    GetWindowThreadProcessId(hwnd, &mut owner);
    if owner != pick.pid {
        return 1;
    }
    let mut rect: RECT = std::mem::zeroed();
    if GetWindowRect(hwnd, &mut rect) == 0 {
        return 1;
    }
    let area = (rect.right - rect.left) as i64 * (rect.bottom - rect.top) as i64;
    if area > pick.area {
        pick.area = area;
        pick.best = hwnd;
    }
    1
}

/// The player's largest visible top-level window; posted input goes here.
pub fn player_window(pid: u32) -> Option<isize> {
    let mut pick = WindowPick { pid, best: 0, area: 0 };
    unsafe { EnumWindows(Some(pick_window), &mut pick as *mut _ as isize) };
    (pick.best != 0).then_some(pick.best)
}

fn post_key(hwnd: isize, vk: usize, count: usize, gap: Duration) {
    for _ in 0..count {
        unsafe {
            PostMessageW(hwnd, WM_KEYDOWN, vk, 0);
            PostMessageW(hwnd, WM_KEYUP, vk, 0);
        }
        std::thread::sleep(gap);
    }
}

/// The range of segment indexes whose key is live right now.
///
/// Reading this from the *keys* rather than from every context the player holds is what makes a
/// seek able to tell whether it has arrived; see the trait's note in `harvest::seek`.
fn live_window(player: &Player) -> Option<(u32, u32)> {
    let live = player.active_keys();
    Some((*live.keys().next()?, *live.keys().next_back()?))
}

/// The real player, presented to the portable seek.
struct LivePlayhead<'a>(&'a Player);

impl Playhead for LivePlayhead<'_> {
    fn window(&self) -> Option<(u32, u32)> {
        live_window(self.0)
    }

    fn step(&self, back: bool, count: usize, gap: Duration) {
        if let Some(hwnd) = player_window(self.0.pid()) {
            post_key(hwnd, if back { VK_LEFT } else { VK_RIGHT }, count, gap);
        }
    }
}

/// Move the playhead so that `target` sits inside the live key window, which is what makes the
/// player decrypt it. Returns false when the seek keys turn out to be unbound, so the caller can
/// stop asking instead of looping forever.
pub fn seek_window_to(
    player: &Player,
    target: u32,
    press_gap: Duration,
    reporter: &Reporter,
) -> Result<bool> {
    // Checked up front so the reason given is the specific one: with no window there is nothing
    // to post to at all, and the seek's own "nothing to aim at" would blame the key window.
    if player_window(player.pid()).is_none() {
        reporter.info(format!(
            "  no visible player window for pid {}; cannot sweep",
            player.pid()
        ));
        return Ok(false);
    }
    seek::seek_window_to(&LivePlayhead(player), target, press_gap, reporter)
}
