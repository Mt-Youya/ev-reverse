//! Driving the player's playhead without touching its UI.
//!
//! A segment the playhead never reached has no key anywhere: the AES schedule only exists once
//! the player decrypts that segment for playback, and nothing in the download path computes it.
//! So a gap left behind the playhead can only be filled by putting the playhead back inside the
//! gap and letting the read-ahead decrypt it again.
//!
//! Posting `WM_KEYDOWN`/`WM_KEYUP` to the player's own window does that without focus, without
//! injection, and without knowing a single UI coordinate — only the window handle is needed.
//! The live key window doubles as a position readout: it starts at the playhead and runs some
//! way ahead of it, so the sweep can measure how far one key press moved things and size the
//! next batch from that rather than assuming a step size.

use crate::process::Player;
use anyhow::Result;
use evmedia_contract::Reporter;
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

/// Move the playhead so that `target` sits inside the live (decrypted) key window, which is
/// what makes the player decrypt it. Returns false when the seek keys turn out to be unbound,
/// so the caller can stop asking instead of looping forever.
pub fn seek_window_to(
    player: &Player,
    target: u32,
    press_gap: Duration,
    reporter: &Reporter,
) -> Result<bool> {
    let Some(hwnd) = player_window(player.pid()) else {
        reporter.info(format!("  no visible player window for pid {}; cannot sweep", player.pid()));
        return Ok(false);
    };
    // Segments moved per key press. The first batch guesses 1 and the measurement corrects it.
    let mut per_press = 1.0f64;
    for round in 1..=8 {
        let live = player.active_keys();
        let (Some(&current), Some(&highest)) = (live.keys().next(), live.keys().next_back()) else {
            reporter.info("  player is not holding a key window; nothing to aim at");
            return Ok(false);
        };
        if current <= target && target <= highest {
            return Ok(true);
        }
        let (vk, distance) = if target < current {
            (VK_LEFT, (current - target) as f64)
        } else {
            (VK_RIGHT, (target - highest) as f64)
        };
        let presses = (distance / per_press).clamp(2.0, 400.0) as usize;
        post_key(hwnd, vk, presses, press_gap);

        let after = player.active_keys();
        let (Some(&moved), Some(&moved_high)) = (after.keys().next(), after.keys().next_back())
        else {
            return Ok(false);
        };
        if moved == current && moved_high == highest {
            reporter.info(format!("  seek keys had no effect (window stayed {current}..{highest})"));
            return Ok(false);
        }
        let travelled = if target < current {
            (current as i64 - moved as i64).max(0) as f64
        } else {
            (moved_high as i64 - highest as i64).max(0) as f64
        };
        per_press = (travelled / presses as f64).max(0.05);
        reporter.info(format!(
            "  sweep {round}: {presses} x VK_{} moved the key window {current}..{highest} -> {moved}..{moved_high} (~{per_press:.2} seg/press)",
            if vk == VK_LEFT { "LEFT" } else { "RIGHT" }
        ));
    }
    let live = player.active_keys();
    let covered = live
        .keys()
        .next()
        .zip(live.keys().next_back())
        .is_some_and(|(low, high)| *low <= target && target <= *high);
    if !covered {
        reporter.info(format!("  sweep did not converge on index {target}"));
    }
    Ok(covered)
}
