# Drives the built window for verification: point it at a fixture, press select-all and run, then
# report what the app itself logged and what landed on disk.
#
# This is a verification script, not part of the product. It exists because "the queue works" is
# only worth claiming if a window was actually driven through it.
#
# ASCII only, deliberately: Windows PowerShell 5.1 reads a .ps1 as ANSI, so non-ASCII text in this
# file would be mangled before it ever ran.
param(
  [string]$Root = "verify_gui",
  [string]$Cli = "target\release\evmedia-stub.exe",
  [string]$Shot = "verify_out_gui.png",
  [int]$Videos = 12,
  [int]$WaitSeconds = 120
)

$ErrorActionPreference = "Stop"
$repo = (Get-Location).Path
$rootAbs = Join-Path $repo $Root
$cfgDir = Join-Path $env:APPDATA 'evmedia-gui'
$logPath = Join-Path $cfgDir 'app.log'

Add-Type -AssemblyName System.Drawing, System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Ui {
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint f);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, IntPtr e);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
"@

function Shot([IntPtr]$handle, [string]$path) {
  $r = New-Object Ui+RECT
  [void][Ui]::GetWindowRect($handle, [ref]$r)
  $w = $r.Right - $r.Left; $h = $r.Bottom - $r.Top
  $bmp = New-Object System.Drawing.Bitmap $w, $h
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $hdc = $g.GetHdc(); [void][Ui]::PrintWindow($handle, $hdc, 2); $g.ReleaseHdc($hdc)
  $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $bmp.Dispose()
  return @{ Width = $w; Height = $h }
}

function Click([IntPtr]$handle, [int]$x, [int]$y, [string]$name) {
  $r = New-Object Ui+RECT
  [void][Ui]::GetWindowRect($handle, [ref]$r)
  [void][Ui]::ShowWindow($handle, 9)   # SW_RESTORE
  [void][Ui]::BringWindowToTop($handle)
  [void][Ui]::SetForegroundWindow($handle)
  Start-Sleep -Milliseconds 400
  [void][Ui]::SetCursorPos(($r.Left + $x), ($r.Top + $y))
  Start-Sleep -Milliseconds 150
  $fore = [Ui]::GetForegroundWindow()
  Write-Output ("click {0} window({1},{2}) foreground={3} ours={4}" -f $name, $x, $y, $fore, ($fore -eq $handle))
  [Ui]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)
  Start-Sleep -Milliseconds 70
  [Ui]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)
  Start-Sleep -Milliseconds 400
}

Get-Process evmedia-gui -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

Remove-Item -Recurse -Force $rootAbs -ErrorAction SilentlyContinue
& cargo run --release -p evmedia-gui --example make_fixture -- $Root $Videos 3 | Out-Null

New-Item -ItemType Directory -Path $cfgDir -Force | Out-Null
$cfg = [ordered]@{
  cliPath      = (Join-Path $repo $Cli)
  session      = (Join-Path $rootAbs 'session.json')
  account      = 119354
  root         = $rootAbs
  workers      = 3
  jobs         = 4
  extension    = 'mp4'
  ffmpeg       = 'ffmpeg'
  ffprobe      = 'ffprobe'
  force        = $false
  lastSelected = @()
}
$cfg | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 (Join-Path $cfgDir 'config.json')
Remove-Item $logPath -ErrorAction SilentlyContinue

$proc = Start-Process -FilePath (Join-Path $repo 'target\release\evmedia-gui.exe') -WorkingDirectory $repo -PassThru
for ($i = 0; $i -lt 60; $i++) { Start-Sleep -Milliseconds 250; $proc.Refresh(); if ($proc.MainWindowHandle -ne 0) { break } }
Start-Sleep -Seconds 5
$proc.Refresh()
$handle = $proc.MainWindowHandle
if ($handle -eq 0) { throw "the window never appeared" }

$size = Shot $handle (Join-Path $repo "verify_out_gui_1_catalog.png")
Write-Output ("window {0}x{1}" -f $size.Width, $size.Height)

# Coordinates from dist/style.css: the top bar is 118 px tall, the middle row's head is 36 px, and
# `.pane-foot` is 42 px tall with 8 px of vertical padding, so a 26 px button is centred 7 px below
# the foot's top edge.
$width = $size.Width
$height = $size.Height
$middleRowTop = 118
$footTop = $height - 42
$buttonY = $footTop + 7 + 13

Click $handle ([int]($width * 0.40) + 150) ($middleRowTop + 20) "select-all"
Shot $handle (Join-Path $repo "verify_out_gui_2_selected.png") | Out-Null
Click $handle ($width - 60) $buttonY "run"

$expected = $Videos
$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Seconds 2
  $done = (Get-ChildItem (Join-Path $rootAbs 'out') -Recurse -File -ErrorAction SilentlyContinue).Count
  if ($done -ge $expected) { break }
}
Start-Sleep -Seconds 4
$proc.Refresh()
if ($proc.HasExited) { throw "the window exited with $($proc.ExitCode)" }
Shot $handle (Join-Path $repo $Shot) | Out-Null

Write-Output "--- app.log ---"
Get-Content $logPath -ErrorAction SilentlyContinue | Select-Object -Last 25
Write-Output "--- outputs ---"
$outputs = Get-ChildItem (Join-Path $rootAbs 'out') -Recurse -File -ErrorAction SilentlyContinue
Write-Output ("outputs={0} of {1}" -f $outputs.Count, $Videos)

Stop-Process -Id $proc.Id -Force
Write-Output "done"
