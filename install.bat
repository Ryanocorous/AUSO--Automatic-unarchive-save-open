@echo off
setlocal
cd /d "%~dp0"
echo Note: If you have not compiled the files yet, either compile them or download a release
echo       Alternatively, use the portable version found in the zip. This isn't as robust, but it works. 
echo ____________________________________________________________________________________________________



echo [I] Install
echo [U] Uninstall


echo ____________________________________________________________________________________________________
set /p AUSO_ACTION=Choose (I / U): 
if /i "%AUSO_ACTION%"=="U" (
    set "AUSO_COMMAND=uninstall"
) else (
    set "AUSO_COMMAND=install"
    if not exist watcher.exe call Build-Watcher-MinGW.bat
    if not exist watcher.exe call Build-Watcher-MSVC.bat
    if not exist watcher.exe (
        echo watcher.exe was not built.
        pause
        exit /b 1
    )
)
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "Auto_Unzip-Save-Open.py" %AUSO_COMMAND%
) else (
    python "Auto_Unzip-Save-Open.py" %AUSO_COMMAND%
)
pause
