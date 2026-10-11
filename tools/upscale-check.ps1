# Compares an upscaled lesson against the file it came from, and answers the two questions that
# matter: did it actually get bigger, and is it still intact?
#
# An upscale is easy to get wrong in ways that look fine: the export can silently fall back to the
# source resolution when RTX VSR is unavailable, and a generative pass can drop or duplicate frames
# without failing anything. Neither shows up in a file listing.
#
# ASCII only, deliberately: Windows PowerShell 5.1 reads a .ps1 as ANSI, so non-ASCII in this file
# would be mangled before it ran. Paths are passed through as arguments, which is safe.
param(
  [Parameter(Mandatory = $true)][string]$Original,
  [Parameter(Mandatory = $true)][string]$Enhanced,
  [double]$Tolerance = 0.5,
  [string]$Frame = ""
)

$ErrorActionPreference = "Continue"
$ffmpeg = if ($env:EVMEDIA_FFMPEG) { $env:EVMEDIA_FFMPEG } else { "ffmpeg" }
$ffprobe = if ($env:EVMEDIA_FFPROBE) { $env:EVMEDIA_FFPROBE } else { "ffprobe" }

# A missing file otherwise reads as "resolution 0x0, no audio, no decode errors", which passes every
# check that is not the one about size.
foreach ($candidate in @($Original, $Enhanced)) {
  if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
    Write-Output ("missing: {0}" -f $candidate)
    exit 2
  }
}

function Get-Media([string]$path) {
  $json = & $ffprobe -v error -show_streams -show_format -of json $path | Out-String
  return $json | ConvertFrom-Json
}

function Show-Side([string]$label, $media, $file) {
  $video = $media.streams | Where-Object { $_.codec_type -eq "video" } | Select-Object -First 1
  $audio = $media.streams | Where-Object { $_.codec_type -eq "audio" } | Select-Object -First 1
  Write-Output ("{0,-9} {1}" -f $label, (Split-Path $file -Leaf))
  Write-Output ("  video    {0}x{1} {2} @ {3}" -f $video.width, $video.height, $video.codec_name, $video.pix_fmt)
  # nb_frames is absent for plenty of containers; it is a display field, never a check.
  $frames = if ("$($video.nb_frames)" -match '^\d+$') { [int]$video.nb_frames } else { 0 }
  Write-Output ("  duration {0:N3} s, {1:N0} frames" -f [double]$media.format.duration, $frames)
  Write-Output ("  size     {0:N1} MB at {1:N0} kbps" -f ([double]$media.format.size / 1MB), ([double]$media.format.bit_rate / 1000))
  if ($audio) {
    Write-Output ("  audio    {0} {1} Hz {2} ch" -f $audio.codec_name, $audio.sample_rate, $audio.channels)
  } else {
    Write-Output "  audio    NONE"
  }
}

$source = Get-Media $Original
$result = Get-Media $Enhanced
Write-Output "=== before / after ==="
Show-Side "original" $source $Original
Show-Side "upscaled" $result $Enhanced

$sv = $source.streams | Where-Object { $_.codec_type -eq "video" } | Select-Object -First 1
$rv = $result.streams | Where-Object { $_.codec_type -eq "video" } | Select-Object -First 1
$sd = [double]$source.format.duration
$rd = [double]$result.format.duration

Write-Output "=== checks ==="
$failed = 0

if ($rv.width -gt $sv.width -and $rv.height -gt $sv.height) {
  Write-Output ("ok    resolution grew {0}x{1} -> {2}x{3} ({4:N2}x)" -f $sv.width, $sv.height, $rv.width, $rv.height, ($rv.width / $sv.width))
} else {
  Write-Output ("FAIL  resolution did not grow ({0}x{1} -> {2}x{3}): the upscale did not run, or fell back to the source size" -f $sv.width, $sv.height, $rv.width, $rv.height)
  $failed++
}

if (($rv.width % 2) -eq 0 -and ($rv.height % 2) -eq 0) {
  Write-Output "ok    output dimensions are even"
} else {
  Write-Output ("WARN  odd output dimension {0}x{1}; most hardware encoders refuse it" -f $rv.width, $rv.height)
}

if ([math]::Abs($rd - $sd) -le $Tolerance) {
  Write-Output ("ok    duration matches within {0:N2} s ({1:N3} s vs {2:N3} s)" -f $Tolerance, $rd, $sd)
} else {
  Write-Output ("FAIL  duration differs by {0:N3} s ({1:N3} s vs {2:N3} s): frames were dropped or duplicated" -f ($rd - $sd), $rd, $sd)
  $failed++
}

$decode = & $ffmpeg -v error -nostdin -xerror -i $Enhanced -map 0:v -map "0:a?" -f null - 2>&1
if (($decode | Measure-Object).Count -eq 0) {
  Write-Output "ok    the upscaled file decodes end to end with no errors"
} else {
  Write-Output "FAIL  decode errors in the upscaled file:"
  $decode | Select-Object -First 5
  $failed++
}

if ($result.streams | Where-Object { $_.codec_type -eq "audio" }) {
  $level = & $ffmpeg -hide_banner -nostdin -i $Enhanced -vn -af volumedetect -f null - 2>&1 |
    Select-String -Pattern "mean_volume"
  Write-Output ("ok    audio present ({0})" -f ($level -join "; ").Trim())
} elseif ($source.streams | Where-Object { $_.codec_type -eq "audio" }) {
  Write-Output "FAIL  the source had audio and the upscaled file does not"
  $failed++
} else {
  Write-Output "ok    neither file has audio"
}

Write-Output "=== quality, against a plain resample of the original ==="
# A reference point, not a verdict: a generative pass is not trying to reproduce a Lanczos upscale,
# so a lower score here is not by itself a regression. Read it next to the frames below.
#
# A 30-second window in the middle rather than the whole file: the question is "did this distort the
# picture", and the answer does not need an hour of decoding to be visible. Both filters report at
# INFO level, so the log level here is deliberately not `error`.
$window = [math]::Max(0, [math]::Round($sd / 2 - 15, 3))
foreach ($metric in @("ssim", "psnr")) {
  $filter = "[1:v]scale={0}:{1}:flags=lanczos[ref];[0:v][ref]{2}" -f $rv.width, $rv.height, $metric
  $reported = & $ffmpeg -v info -nostdin -ss $window -t 30 -i $Enhanced -ss $window -t 30 -i $Original `
    -lavfi $filter -f null - 2>&1 | Select-String -Pattern $metric.ToUpper() | Select-Object -Last 1
  if ($reported) {
    Write-Output ("      {0}" -f $reported.Line.Trim())
  } else {
    Write-Output ("      {0}: not reported" -f $metric.ToUpper())
  }
}

$stamp = if ($Frame) { $Frame } else { [string]([math]::Round($sd / 2, 3)) }
$stem = [System.IO.Path]::GetFileNameWithoutExtension($Enhanced)
foreach ($pair in @(@($Original, "original"), @($Enhanced, "upscaled"))) {
  $png = Join-Path (Split-Path $Enhanced -Parent) ("{0}-{1}-at{2}s.png" -f $stem, $pair[1], $stamp)
  & $ffmpeg -v error -nostdin -y -ss $stamp -i $pair[0] -frames:v 1 $png 2>&1 | Out-Null
  Write-Output ("frame   {0}" -f $png)
}
Write-Output "Open the two PNGs at 100% and read the smallest text: that is the only check this script cannot make."

if ($failed -gt 0) {
  Write-Output ("=== {0} check(s) failed ===" -f $failed)
  exit 1
}
Write-Output "=== all checks passed ==="
