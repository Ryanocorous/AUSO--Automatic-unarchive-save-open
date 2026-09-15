@echo off
setlocal
cd /d "%~dp0"
where cl.exe >nul 2>nul
if errorlevel 1 (
    echo cl.exe was not found. Run this from a Visual Studio Developer Command Prompt.
    exit /b 1
)
cl.exe /nologo /std:c++17 /O2 /EHsc /MT /DUNICODE /D_UNICODE src\watcher.cpp src\runtime.cpp /Isrc /Fe:watcher.exe /link /SUBSYSTEM:WINDOWS shell32.lib wtsapi32.lib userenv.lib advapi32.lib
if errorlevel 1 exit /b 1
echo Built: %CD%\watcher.exe
