@echo off
cd /d "%~dp0"
python autoshield.py
set "script_exit=%ERRORLEVEL%"
if not "%script_exit%"=="0" (
    echo.
    echo Script stopped with error code %script_exit%.
    pause
)
exit %script_exit%
