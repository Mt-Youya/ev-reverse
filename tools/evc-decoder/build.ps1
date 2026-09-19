# Build the local-file-only compatibility decoder; no player DLL is loaded at runtime.
param(
    [string]$BuildRoot = (Join-Path $PSScriptRoot '../../target/evc-build'),
    [string]$GitRoot = '',
    [string]$VcVars = '',
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
$BuildRoot = (Resolve-Path -LiteralPath $BuildRoot).Path
if (!$GitRoot) {
    $git = (Get-Command git.exe).Source
    $GitRoot = Split-Path (Split-Path $git)
}
if (!$VcVars) {
    $roots = @("${env:ProgramFiles(x86)}/Microsoft Visual Studio", "$env:ProgramFiles/Microsoft Visual Studio")
    $VcVars = Get-ChildItem $roots -Filter vcvars64.bat -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
}
if (!(Test-Path -LiteralPath $VcVars)) { throw 'MSVC C++ Build Tools not found; pass -VcVars.' }
$bash = Join-Path $GitRoot 'bin/bash.exe'
if (!(Test-Path -LiteralPath $bash)) { throw 'Git Bash not found; pass -GitRoot.' }
& $Python --version
if ($LASTEXITCODE) { throw 'Python 3 is required; pass -Python.' }

function Fetch-Checked($url, $name, $hash) {
    $archive = Join-Path $BuildRoot $name
    if (!(Test-Path -LiteralPath $archive)) { Invoke-WebRequest -Uri $url -OutFile $archive }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $hash) {
        throw "Checksum mismatch: $archive"
    }
    & tar -xf $archive -C $BuildRoot
    if ($LASTEXITCODE) { throw "Cannot extract $archive" }
}
Fetch-Checked 'https://ffmpeg.org/releases/ffmpeg-4.2.9.tar.xz' 'ffmpeg-4.2.9.tar.xz' `
    '4974d62e7507ba3b26fa5f30af8ee36825917ddb4a1ad4118277698c1c8818cf'
Fetch-Checked 'https://mirror.msys2.org/msys/x86_64/make-4.4.1-3-x86_64.pkg.tar.zst' 'make.tar.zst' `
    'af0bdba17f06fe037f0194069adaa31a8fe45f1a11381501896aea1fae37bd5d'
$source = Join-Path $BuildRoot 'ffmpeg-4.2.9'
& $Python (Join-Path $PSScriptRoot 'patch.py') $source
if ($LASTEXITCODE) { throw 'Decoder patch failed.' }
$shellScript = Join-Path $BuildRoot 'compile.sh'
@'
set -eu
cd "$(dirname "$0")/ffmpeg-4.2.9"
./configure --toolchain=msvc --arch=x86_64 --target-os=win64 --disable-everything --disable-autodetect --disable-x86asm --disable-inline-asm --disable-doc --disable-debug --disable-network --disable-avdevice --disable-postproc --disable-ffprobe --enable-ffmpeg --enable-decoder=h264,aac --enable-parser=h264,aac --enable-demuxer=mpegts,h264 --enable-protocol=file,pipe --enable-muxer=nut,null,rawvideo --enable-encoder=rawvideo,wrapped_avframe --enable-filter=buffer,buffersink,null,anull,format --extra-cflags=-O2
make -j8
'@ | Set-Content -LiteralPath $shellScript -Encoding ascii
$batch = Join-Path $BuildRoot 'compile.cmd'
@"
@echo off
call "$VcVars"
if errorlevel 1 exit /b 1
set "PATH=$BuildRoot\usr\bin;$GitRoot\usr\bin;%PATH%"
set MSYS2_PATH_TYPE=inherit
"$bash" "$($shellScript.Replace('\','/'))"
"@ | Set-Content -LiteralPath $batch -Encoding ascii
& cmd /d /c $batch
if ($LASTEXITCODE) { throw 'Decoder build failed.' }
$release = Join-Path $repo 'target/release'
New-Item -ItemType Directory -Force -Path $release | Out-Null
Copy-Item -LiteralPath (Join-Path $source 'ffmpeg.exe') -Destination (Join-Path $release 'evmedia-ffmpeg.exe')
Copy-Item -LiteralPath (Join-Path $source 'COPYING.LGPLv2.1') -Destination (Join-Path $release 'evmedia-ffmpeg-LICENSE.txt')
Write-Output "Built $release/evmedia-ffmpeg.exe"
