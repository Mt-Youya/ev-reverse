@echo off
setlocal
set "INPUT=%~dp0EVPlayer2_download.zip"
if not exist "%INPUT%" set "INPUT=%~dp0..\EVPlayer2_download.zip"
if not exist "%INPUT%" (
  echo Missing EVPlayer2_download.zip beside or inside this tool folder.
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0EVPlayer2_capture_keys.ps1" -InputPath "%INPUT%" -OutputDir "%~dp0"
pause
