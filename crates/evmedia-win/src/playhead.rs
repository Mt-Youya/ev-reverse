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
//! This module is the input-posting half only. *Which* way to step, how far, and when to give up is
//! `harvest::seek`, which is portable and tested without a player. The window it aims at is read by
//! `WinSource`, which is what implements `Playhead` for the real thing.

use crate::process::Player;
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
/// The key that steps it back by one increment.
pub const STEP_BACK: usize = VK_LEFT;

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
///
/// `None` means there is nothing to post to at all, which is a different failure from a key that is
/// bound to nothing — and the one worth naming, because no amount of retrying fixes it.
pub fn player_window(pid: u32) -> Option<isize> {
    let mut pick = WindowPick { pid, best: 0, area: 0 };
    unsafe { EnumWindows(Some(pick_window), &mut pick as *mut _ as isize) };
    (pick.best != 0).then_some(pick.best)
}

/// Post `count` key presses to a window, with `gap` between them.
///
/// The delay is not decoration: posting a burst with no gap outruns the player's decryption, and
/// the playhead lands past segments that never got decrypted — which is how gaps are made.
pub fn post_key(hwnd: isize, vk: usize, count: usize, gap: Duration) {
    for _ in 0..count {
        unsafe {
            PostMessageW(hwnd, WM_KEYDOWN, vk, 0);
            PostMessageW(hwnd, WM_KEYUP, vk, 0);
        }
        std::thread::sleep(gap);
    }
}
