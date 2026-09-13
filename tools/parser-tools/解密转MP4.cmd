@echo off
setlocal
cd /d "%~dp0"
if not exist evplayer2_manifest.json (
  echo [ERROR] evplayer2_manifest.json not found. Run 一键抓取.cmd while the video plays first.
  pause
  exit /b 1
)
python "%~dp0EVPlayer2通用解密工具\EVPlayer2_decode.py" --input "D:\Downloads\EVPlayer2Downloads" --manifest "%~dp0evplayer2_manifest.json" --output "%~dp0decoded.ts"
if errorlevel 1 goto :end
ffmpeg -hide_banner -nostdin -i "%~dp0decoded.ts" -map 0:v:0 -map 0:a:0 -c copy -movflags +faststart "%~dp0decoded.mp4"
echo.
if exist "%~dp0decoded.mp4" echo [OK] decoded.mp4 created in this folder.
:end
pause
