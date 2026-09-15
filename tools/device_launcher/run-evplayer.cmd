@echo off
setlocal
set "DIR=%~dp0"
call :main > "%DIR%run-evplayer.log" 2>&1
type "%DIR%run-evplayer.log"
echo.
echo Output saved to "%DIR%run-evplayer.log"
pause
exit /b

:main
cd /d "%DIR%"

if not exist EVDeviceLauncher.exe (
  echo [ERROR] EVDeviceLauncher.exe not found. Run build.cmd first and make sure it says [OK].
  exit /b 1
)
if not exist device-profile.ini (
  echo [ERROR] device-profile.ini not found.
  exit /b 1
)

echo Starting EVPlayer2 with spoofed sandbox identity...
EVDeviceLauncher.exe "D:\Learning\EVPlayer2\EVPlayer2.exe" "%DIR%device-profile.ini"
if errorlevel 1 (
  echo.
  echo [FAILED] See launcher.log in this folder for details.
  exit /b 1
)

echo.
echo [OK] Identity getters installed and debugger detached.
echo Check launcher.log for the SUCCESS line, then verify with:
echo   tools\device_analysis\DeviceSnapshot.exe ^<EVPlayer2 PID^> snapshot.json
exit /b 0
