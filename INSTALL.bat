@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found.
    echo Install 64-bit Python 3.11 or 3.12 and enable "Add Python to PATH".
    echo https://www.python.org/downloads/windows/
    pause
    exit /b 1
)

python -m pip install --upgrade pip
if errorlevel 1 goto :error

python -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo Installation completed successfully.
pause
exit /b 0

:error
echo.
echo Installation failed. Review the error messages above.
pause
exit /b 1

