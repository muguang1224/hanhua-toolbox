@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PY=python"
    ) else (
        echo Python not found. Install Python 3.9+ from python.org first.
        pause
        exit /b 1
    )
)

%PY% build.py
if errorlevel 1 (
    echo.
    echo Build failed. Read the errors above.
    pause
    exit /b 1
)

echo.
echo Done. The exe is in the dist folder.
pause
exit /b 0
