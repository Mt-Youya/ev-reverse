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
@'
EVMedia Windows x64 桌面版

解压到独立文件夹后双击 Launch.cmd。请保留整个目录，EXE 需要同目录内的配套程序。
包含桌面端、CLI、兼容解码器、FFmpeg、ffprobe 和会话获取脚本。
“自动获取”需要已登录的 EVPlayer2，以及安装 frida 的 Python；可用 EVMEDIA_PYTHON 指定解释器。
WebView2、Python/Frida 和 EVPlayer2 使用本机安装，不包含在本压缩包中。

本次修复：
- 从已解密的 lesson.ts 探测视频参数，避免读取 enc 中的密文。
- 未验证的临时视频只保存在 work 目录；音视频完整校验通过后才发布最终 MP4。
- 显示视频转换百分比与校验阶段；下载完成不等于导出完成。
- EVC 视频优先使用 NVIDIA NVENC 无损编码；不可用或失败时自动回退 CPU 编码。
- 同一批次的 EVC 兼容转换会串行执行，避免多个无损转码同时占满 CPU。
- 修复音轨时间戳回退引起的最终封装失败（Non-monotonic DTS），保留成品严格解码校验。

压缩包不包含登录会话、课程密钥、下载缓存或课程视频。
兼容解码器的构建脚本和补丁位于 tools/evc-decoder；其源码下载地址与校验和包含在脚本内。
普通 FFmpeg 构建来源：https://www.gyan.dev/ffmpeg/builds/ ，许可证见 FFmpeg-LICENSE.txt。
'@ | Set-Content -LiteralPath (Join-Path $package '使用说明.txt') -Encoding utf8
$zip = "$package.zip"
Compress-Archive -LiteralPath $package -DestinationPath $zip -CompressionLevel Fastest
Get-Item -LiteralPath $zip | Select-Object FullName, Length
