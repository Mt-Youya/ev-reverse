@echo off
setlocal
set "DIR=%~dp0"
call :main > "%DIR%build.log" 2>&1
type "%DIR%build.log"
echo.
echo Output saved to "%DIR%build.log"
pause
exit /b

:main
echo === EVDeviceLauncher build %date% %time% ===
set "VCVARS="

rem 1) VS2022 default paths
for %%V in (Community Professional Enterprise BuildTools) do (
  if not defined VCVARS if exist "C:\Program Files\Microsoft Visual Studio\2022\%%V\VC\Auxiliary\Build\vcvars64.bat" set "VCVARS=C:\Program Files\Microsoft Visual Studio\2022\%%V\VC\Auxiliary\Build\vcvars64.bat"
)

rem 2) VS2019 default paths
if not defined VCVARS (
  for %%V in (Community Professional Enterprise BuildTools) do (
    if not defined VCVARS if exist "C:\Program Files (x86)\Microsoft Visual Studio\2019\%%V\VC\Auxiliary\Build\vcvars64.bat" set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2019\%%V\VC\Auxiliary\Build\vcvars64.bat"
  )
)

rem 3) vswhere for any other install location
if not defined VCVARS if exist "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe" (
  for /f "usebackq tokens=*" %%I in (`"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do (
    if not defined VCVARS if exist "%%I\VC\Auxiliary\Build\vcvars64.bat" set "VCVARS=%%I\VC\Auxiliary\Build\vcvars64.bat"
  )
)

if not defined VCVARS (
  echo [ERROR] Could not find vcvars64.bat.
  echo   Checked VS2022/VS2019 default paths and vswhere.
  echo   If VS is installed on another drive, tell me the install folder
  echo   and I will fix build.cmd. Alternatively copy the EVDeviceLauncher.exe
  echo   from D:\codes\git\ev-reverse\tools\device_launcher\ if it still exists.
  exit /b 1
)

echo Found: %VCVARS%
call "%VCVARS%" >nul
if errorlevel 1 (
  echo [ERROR] vcvars64.bat failed with errorlevel %errorlevel%
  exit /b 1
)

cd /d "%DIR%"
cl /nologo /std:c++17 /EHsc /W4 /O2 /MT /DUNICODE /D_UNICODE EVDeviceLauncher.cpp /Fe:EVDeviceLauncher.exe /link /SUBSYSTEM:WINDOWS
if errorlevel 1 (
  echo [ERROR] Compile failed. See cl output above.
  exit /b 1
)

echo.
echo [OK] EVDeviceLauncher.exe built successfully.
exit /b 0
