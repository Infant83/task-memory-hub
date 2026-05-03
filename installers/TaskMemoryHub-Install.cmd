@echo off
setlocal
set SCRIPT_DIR=%~dp0
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%..\scripts\install-tmh.ps1" -RegisterHubStation -StartNow -DesktopShortcuts
echo.
echo Press any key to close.
pause >nul
