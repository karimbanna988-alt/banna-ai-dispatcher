# Equivalent to "Dispatcher Status.bat" -- shells out to the same
# tested Python module every other entry point uses.
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $ProjectRoot
python "tools\windows_launcher\launcher.py" status
