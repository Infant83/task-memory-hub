@echo off
setlocal
set SCRIPT_DIR=%~dp0
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%..\scripts\uninstall-tmh-hub-station.ps1" -StopNow
echo.
echo Press any key to close.
pause >nul
