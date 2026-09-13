"""Drive the EVPlayer2 playhead with arrow keys, using only the standard library.

The player is a Qt window, so it reads keyboard through its own Windows event dispatcher and a
posted WM_KEYDOWN is enough -- no foreground focus, no injection, no synthetic input at the
desktop level. `playhead.rs` in the Rust tree does the same thing for the product; this is the
standalone version used while probing.

Usage:
    python drive_playhead.py <window-hwnd-hex> <presses> [key] [gap-ms]

`key` defaults to right (0x27). Left is 0x25.
"""

import ctypes
import ctypes.wintypes as wt
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
VK_LEFT = 0x25
VK_RIGHT = 0x27


def main():
    hwnd = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x00680668
    presses = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    key = int(sys.argv[3], 0) if len(sys.argv) > 3 else VK_RIGHT
    gap = (int(sys.argv[4]) if len(sys.argv) > 4 else 120) / 1000.0

    if not user32.IsWindow(hwnd):
        print("hwnd 0x%08X is not a window" % hwnd)
        return 1

    print("driving hwnd=0x%08X, key=0x%02X, %d press(es), gap=%.0fms"
          % (hwnd, key, presses, gap * 1000))
    for i in range(presses):
        # Scan code in lParam bits 16-23; Qt cares about the key code but some paths want it.
        lparam = 1 | (0x4D << 16)
        user32.PostMessageW(hwnd, WM_KEYDOWN, key, lparam)
        time.sleep(0.012)
        user32.PostMessageW(hwnd, WM_KEYUP, key, lparam | (1 << 30) | (1 << 31))
        if (i + 1) % 10 == 0:
            print("  %d" % (i + 1), flush=True)
        time.sleep(gap)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
