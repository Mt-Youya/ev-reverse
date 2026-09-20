param(
    [string]$OutputRoot = (Join-Path $PSScriptRoot '../target/packages'),
    [string]$BuildOutput = ''
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ([string]::IsNullOrWhiteSpace($BuildOutput)) {
    $BuildOutput = Join-Path $repo 'target/release'
}
$BuildOutput = (Resolve-Path $BuildOutput).Path
$name = 'evmedia-windows-x64-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$package = Join-Path $OutputRoot $name
New-Item -ItemType Directory -Path $package -Force | Out-Null
foreach ($file in @('evmedia-gui.exe', 'evmedia.exe', 'evmedia-ffmpeg.exe', 'evmedia-ffmpeg-LICENSE.txt')) {
    Copy-Item -LiteralPath (Join-Path $BuildOutput $file) -Destination $package
}
foreach ($tool in @('ffmpeg', 'ffprobe')) {
    $binary = Get-Item (Get-Command "$tool.exe").Source
    $source = if ($binary.Target) { $binary.Target } else { $binary.FullName }
    Copy-Item -LiteralPath $source -Destination (Join-Path $package "$tool.exe")
    $license = Join-Path (Split-Path (Split-Path $source)) 'LICENSE'
    if (Test-Path -LiteralPath $license) {
        Copy-Item -LiteralPath $license -Destination (Join-Path $package 'FFmpeg-LICENSE.txt')
    }
}
$probeDir = Join-Path $package 'tools/parser-tools'
New-Item -ItemType Directory -Path $probeDir -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $repo 'tools/parser-tools/probe_catalog_constants.py') -Destination $probeDir
Copy-Item -LiteralPath (Join-Path $repo 'tools/evc-decoder') -Destination (Join-Path $package 'tools') -Recurse
@'
@echo off
setlocal
cd /d "%~dp0"
set "PATH=%~dp0;%PATH%"
start "" "%~dp0evmedia-gui.exe"
'@ | Set-Content -LiteralPath (Join-Path $package 'Launch.cmd') -Encoding ascii
@(
    'EVMedia Windows x64 desktop package', '',
    'Extract into a dedicated folder and launch Launch.cmd. Keep all files together.',
    'Includes the desktop app, CLI, EVC decoder, FFmpeg, ffprobe, and session helper.', '',
    'Changes:',
    '- EVC uses lossless NVIDIA NVENC when available, then falls back to CPU encoding.',
    '- EVC compatibility conversion is serialized to prevent CPU/GPU contention.',
    '- Batch export uses one global segment queue: all videos share download and decrypt permits.',
    '- A downloaded segment is decrypted immediately and merged in its own lesson order.', '',
    'The package does not contain sessions, course keys, downloads, or course videos.',
    'FFmpeg source: https://www.gyan.dev/ffmpeg/builds/ ; see FFmpeg-LICENSE.txt.'
) | Set-Content -LiteralPath (Join-Path $package 'README.txt') -Encoding utf8
$zip = "$package.zip"
Compress-Archive -LiteralPath $package -DestinationPath $zip -CompressionLevel Fastest
Get-Item -LiteralPath $zip | Select-Object FullName, Length
