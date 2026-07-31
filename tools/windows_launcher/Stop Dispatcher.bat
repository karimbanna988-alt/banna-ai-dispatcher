@echo off
cd /d "%~dp0..\.."
python "tools\windows_launcher\launcher.py" stop
echo.
echo Press any key to close this window.
pause >nul
