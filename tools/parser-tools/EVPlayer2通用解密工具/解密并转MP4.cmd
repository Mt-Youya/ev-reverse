@echo off
setlocal
set "BASE=%~dp0"
set "INPUT=%BASE%EVPlayer2_download.zip"
if not exist "%INPUT%" set "INPUT=%BASE%..\EVPlayer2_download.zip"
if not exist "%BASE%evplayer2_manifest.json" (
  echo Missing evplayer2_manifest.json. Run 生成参数.cmd inside the active EVPlayer2 session first.
  pause
  exit /b 1
)
python "%BASE%EVPlayer2_decode.py" --input "%INPUT%" --manifest "%BASE%evplayer2_manifest.json" --output "%BASE%decoded.ts"
if errorlevel 1 goto :end
ffmpeg -hide_banner -nostdin -i "%BASE%decoded.ts" -map 0:v:0 -map 0:a:0 -c copy -movflags +faststart "%BASE%decoded.mp4"
:end
pause
