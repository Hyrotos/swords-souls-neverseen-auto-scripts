@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Run INSTALL.bat after installing Python.
    pause
    exit /b 1
)

echo Swords ^& Souls Neverseen - Auto Training Scripts
echo.
echo 1. Sword
echo 2. Archery
echo 3. Magic
echo 4. Shield
echo 5. Agility
echo 0. Exit
echo.
set /p mode=Choose a mode: 

if "%mode%"=="1" goto :sword
if "%mode%"=="2" goto :archery
if "%mode%"=="3" goto :magic
if "%mode%"=="4" goto :shield
if "%mode%"=="5" goto :agility
if "%mode%"=="0" exit /b 0

echo Invalid selection.
pause
exit /b 1

:sword
cd /d "%~dp0sword"
python autoplayer_fixed.py
goto :done

:archery
cd /d "%~dp0archery"
python autoarcher.py
goto :done

:magic
cd /d "%~dp0magic"
python automagic.py
goto :done

:shield
cd /d "%~dp0shield"
python autoshield.py
goto :done

:agility
cd /d "%~dp0agility"
python autododge.py

:done
set "script_exit=%ERRORLEVEL%"
if not "%script_exit%"=="0" (
    echo.
    echo Script stopped with error code %script_exit%.
    pause
)
exit /b %script_exit%
