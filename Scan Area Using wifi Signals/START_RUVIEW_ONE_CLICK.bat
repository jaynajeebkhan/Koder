@echo off
setlocal
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0START_RUVIEW_ONE_CLICK.ps1"
pause

