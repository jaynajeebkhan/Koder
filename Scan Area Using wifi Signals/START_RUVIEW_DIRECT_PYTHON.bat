@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found in PATH.
    echo Install Python, then double-click this file again.
    pause
    exit /b 1
)

python "%~dp0ruview_python_localhost.py" --host 127.0.0.1 --port 3000 --open-browser
pause

