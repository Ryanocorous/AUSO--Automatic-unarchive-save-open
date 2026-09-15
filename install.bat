@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "Auto_Unzip-Save-Open.py" install
) else (
    python "Auto_Unzip-Save-Open.py" install
)
pause
