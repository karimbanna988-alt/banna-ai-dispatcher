@echo off
cd /d "%~dp0..\.."
python "tools\windows_launcher\launcher.py" start
echo.
echo Press any key to close this window (the dispatcher keeps running).
pause >nul
