# Windows Launcher

For plain-English usage instructions, see
[`docs/start_dispatcher_windows.md`](../../docs/start_dispatcher_windows.md).

This directory is the implementation:

- `launcher.py` -- the only place with real logic (starting/stopping/
  checking the two processes, PID-file tracking, tunnel URL detection).
  Fully unit-tested with mocked processes in
  `tests/test_windows_launcher.py` -- no real process is ever started
  by the test suite.
- `Start Dispatcher.bat` / `Stop Dispatcher.bat` / `Dispatcher Status.bat` /
  `Open Dispatcher.bat` -- double-click entry points for a non-technical
  user. Each just runs `python tools\windows_launcher\launcher.py <verb>`.
- `start_dispatcher.ps1` / `stop_dispatcher.ps1` / `status_dispatcher.ps1` /
  `open_dispatcher.ps1` -- the same, for anyone who prefers "Run with
  PowerShell" over double-clicking a `.bat` file.

Runtime state (PID files, process logs, the last-known tunnel URL)
lives under `.deploy_logs/launcher/` at the project root -- never
committed, never containing `.env` values or the login password.
