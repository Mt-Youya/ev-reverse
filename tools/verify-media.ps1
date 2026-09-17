# Checks whether an exported file is actually watchable: real picture and real sound.
#
# The project has been wrong about "the export worked" twice, and both times a structural check said
# yes while the picture or the sound said no. This runs the checks that can disagree: decode every
# frame and every sample, and measure the audio instead of trusting that a stream is listed.
#
# ASCII only, deliberately: Windows PowerShell 5.1 reads a .ps1 as ANSI, so non-ASCII in this file
# would be mangled before it ran. Paths are passed through as arguments, which is safe.
param(
  [Parameter(Mandatory = $true)][string]$Path
)

$ErrorActionPreference = "Continue"
$file = Get-Item -LiteralPath $Path
Write-Output ("file   : {0} ({1:N0} MB)" -f $file.Name, ($file.Length / 1MB))

Write-Output "--- streams ---"
& ffprobe -v error -show_entries stream=index,codec_type,codec_name,channels,sample_rate,width,height `
  -of default=noprint_wrappers=1 $file.FullName

Write-Output "--- duration ---"
& ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 $file.FullName

Write-Output "--- audio level (mean_volume near -91 dB means silence) ---"
& ffmpeg -hide_banner -nostdin -i $file.FullName -vn -af volumedetect -f null - 2>&1 |
  Select-String -Pattern 'mean_volume|max_volume'

Write-Output "--- full decode, errors only ---"
$video = & ffmpeg -v error -xerror -i $file.FullName -map 0:v -f null - 2>&1
$audio = & ffmpeg -v error -xerror -i $file.FullName -map 0:a -f null - 2>&1
Write-Output ("video decode error lines: {0}" -f ($video | Measure-Object).Count)
Write-Output ("audio decode error lines: {0}" -f ($audio | Measure-Object).Count)
$video | Select-Object -First 5
$audio | Select-Object -First 5

Write-Output "--- audio coverage (a stream that stops early is a silent second half) ---"
$times = & ffprobe -v error -select_streams a:0 -show_entries packet=pts_time -of csv=p=0 $file.FullName
$values = $times | Where-Object { $_ -and $_ -ne 'N/A' } | ForEach-Object { [double]$_ }
if ($values.Count -gt 0) {
  Write-Output ("audio packets: {0}, {1:N1}s .. {2:N1}s" -f $values.Count, $values[0], $values[-1])
} else {
  Write-Output "audio packets: NONE -- this file has no sound"
}
