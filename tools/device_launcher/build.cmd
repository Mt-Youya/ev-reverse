@echo off
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
cd /d "%~dp0"
cl /nologo /std:c++17 /EHsc /W4 /O2 /MT /DUNICODE /D_UNICODE EVDeviceLauncher.cpp /Fe:EVDeviceLauncher.exe /link /SUBSYSTEM:WINDOWS
exit /b %errorlevel%
