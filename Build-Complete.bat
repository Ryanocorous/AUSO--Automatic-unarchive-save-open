@echo off
setlocal
cd /d "%~dp0"
if exist watcher.exe del /q watcher.exe
call Build-Watcher-MinGW.bat
if exist watcher.exe exit /b 0
call Build-Watcher-MSVC.bat
if exist watcher.exe exit /b 0
echo.
echo Build failed. Install MinGW-w64 or use a Visual Studio Developer Command Prompt.
exit /b 1
