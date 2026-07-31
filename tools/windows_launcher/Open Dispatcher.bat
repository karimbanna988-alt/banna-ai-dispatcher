@echo off
cd /d "%~dp0..\.."
python "tools\windows_launcher\launcher.py" open
echo.
echo Press any key to close this window.
pause >nul
