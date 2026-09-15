# Post keystrokes to the player's own window, the way `evmedia-win::playhead` does.
#
# No focus, no injection, no coordinates: `PostMessageW` to the window the process owns. Used here
# to drive the player's list with the arrow keys so the API flows a human would trigger by clicking
# happen while a probe is attached.
param(
    [Parameter(Mandatory = $true)][int]$ProcessId,
    [string]$Keys = "down,down,return",
    [int]$GapMs = 400
)

Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public class Keys {
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr param);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool PostMessageW(IntPtr hwnd, uint msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowTextW(IntPtr hwnd, System.Text.StringBuilder text, int max);

    delegate bool EnumProc(IntPtr hwnd, IntPtr param);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }

    const uint WM_KEYDOWN = 0x0100, WM_KEYUP = 0x0101;

    public static IntPtr Find(uint pid) {
        IntPtr best = IntPtr.Zero;
        long bestArea = 0;
        EnumWindows((hwnd, param) => {
            if (!IsWindowVisible(hwnd)) return true;
            uint owner; GetWindowThreadProcessId(hwnd, out owner);
            if (owner != pid) return true;
            RECT rect; GetWindowRect(hwnd, out rect);
            long area = (long)(rect.Right - rect.Left) * (rect.Bottom - rect.Top);
            if (area > bestArea) { bestArea = area; best = hwnd; }
            return true;
        }, IntPtr.Zero);
        return best;
    }

    public static string Title(IntPtr hwnd) {
        var text = new System.Text.StringBuilder(512);
        GetWindowTextW(hwnd, text, text.Capacity);
        return text.ToString();
    }

    public static void Press(IntPtr hwnd, int vk) {
        PostMessageW(hwnd, WM_KEYDOWN, (IntPtr)vk, IntPtr.Zero);
        System.Threading.Thread.Sleep(30);
        PostMessageW(hwnd, WM_KEYUP, (IntPtr)vk, IntPtr.Zero);
    }
}
"@

$vk = @{ "left" = 0x25; "up" = 0x26; "right" = 0x27; "down" = 0x28; "return" = 0x0D; "enter" = 0x0D;
         "space" = 0x20; "escape" = 0x1B; "tab" = 0x09; "f5" = 0x74 }
$hwnd = [Keys]::Find($ProcessId)
if ($hwnd -eq [IntPtr]::Zero) { Write-Output "no visible window for pid $ProcessId"; exit 1 }
Write-Output "window: '$([Keys]::Title($hwnd))'  hwnd=$hwnd"
foreach ($name in $Keys.Split(",")) {
    $name = $name.Trim().ToLower()
    if (-not $vk.ContainsKey($name)) { Write-Output "unknown key '$name'"; continue }
    [Keys]::Press($hwnd, $vk[$name])
    Write-Output "posted $name"
    Start-Sleep -Milliseconds $GapMs
}
