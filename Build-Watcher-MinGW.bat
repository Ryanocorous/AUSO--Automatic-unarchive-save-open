@echo off
setlocal
cd /d "%~dp0"
set "GXX="
for %%G in (x86_64-w64-mingw32-g++.exe g++.exe) do (
    for /f "delims=" %%P in ('where %%G 2^>nul') do if not defined GXX set "GXX=%%P"
)
if not defined GXX if exist "C:\Mingw\Mingw64\bin\g++.exe" set "GXX=C:\Mingw\Mingw64\bin\g++.exe"
if not defined GXX if exist "C:\Mingw\mingw64\bin\g++.exe" set "GXX=C:\Mingw\mingw64\bin\g++.exe"
if not defined GXX if exist "C:\mingw64\bin\g++.exe" set "GXX=C:\mingw64\bin\g++.exe"
if not defined GXX if exist "C:\msys64\mingw64\bin\g++.exe" set "GXX=C:\msys64\mingw64\bin\g++.exe"
if not defined GXX (
    echo MinGW-w64 g++.exe was not found.
    exit /b 1
)
echo Using: %GXX%
"%GXX%" -std=c++17 -O2 -s -municode -mwindows -static -Isrc src\watcher.cpp src\runtime.cpp -o watcher.exe -lshell32 -lwtsapi32 -luserenv -ladvapi32
if errorlevel 1 exit /b 1
echo Built: %CD%\watcher.exe
