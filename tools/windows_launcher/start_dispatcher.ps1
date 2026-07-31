# Equivalent to "Start Dispatcher.bat" for users who prefer right-click
# "Run with PowerShell". Does nothing but shell out to the same tested
# Python module every other entry point uses -- no separate logic here.
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $ProjectRoot
python "tools\windows_launcher\launcher.py" start
