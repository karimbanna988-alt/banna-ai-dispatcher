# Quo Ingestion Architecture -- What Actually Triggers a Poll

This document resolves a contradiction between two prior reports: one
said ingestion was "Sync-button-driven with no standalone poller," a
later one attributed new `quo_pending_messages` rows to a "background
poller." Both were describing real, separate mechanisms that exist in
this codebase -- neither report was wrong, but neither was complete on
its own. This is the full picture, based on direct code and
running-process inspection (not assumption).

## There are exactly two ways a poll can happen

### 1. The Sync button in `luxor_desk.py` (the canonical, currently
deployed app)

[luxor_desk_pages/dispatch.py](../luxor_desk_pages/dispatch.py) renders one button via
`shared_desk.render_compact_new_jobs_header(conn)`. When clicked, the
*same* Streamlit script run:

```python
client = quo_client.QuoClient(api_key=api_key)
outcome = poll_for_new_messages(conn, client)
```

calls `dispatcher.quo_ingest.poll_for_new_messages()` synchronously,
inline, in the same request/response cycle as the click. There is no
thread, no timer, no scheduled callback. The call finishes (or fails)
before the page re-renders. **This only ever happens when a logged-in
human taps "Sync."** Streamlit re-rendering the page for any other
reason (navigating tabs, another widget changing, the page simply being
open) does **not** call this code path -- it only executes inside the
`if sync_clicked:` branch, which is only true on the run where that
specific button was the trigger.

There is a client-side auto-refresh in the **older**,
non-canonical `luxor_console_pages/dispatch_queue.py` (the "Speed
Dispatch Console," not `luxor_desk.py`) that clicks a hidden button
every 4 seconds via JavaScript. That auto-refresh reruns the script to
re-read already-stored SQLite data -- its own comment states plainly
"it never calls Quo (Quo is only ever called from the Sync button
above)." It does not affect `luxor_desk.py` at all, since `luxor_desk.py`
doesn't import or use that page.

### 2. `scripts/background_poller.py` -- standalone, human-started, never automatic

This is a plain Python script, not a service, not a Windows Scheduled
Task, not added to startup, not spawned by `luxor_desk.py` or anything
else in this codebase (confirmed: no `subprocess.Popen`/`subprocess.run`
call anywhere in production code references this file). A human must
run it themselves in a terminal:

```
python scripts/background_poller.py            # one poll, then exit
python scripts/background_poller.py --loop      # poll every 300s until Ctrl+C
```

Its own docstring states the operating model precisely: "Not a Windows
service. Not a Scheduled Task. Not added to startup. Does not run
automatically after a reboot. ... a plain Python script that does
something only for as long as a human keeps its process running in a
terminal they started themselves." Closing that terminal, Ctrl+C, or a
machine restart all stop it the same way -- by the process no longer
existing. Nothing brings it back automatically.

It reuses the exact same `poll_for_new_messages()` function the Sync
button calls -- there is only one ingestion/dedup/checkpoint
implementation in this codebase, not two.

A `QUO_POLL_MIN_INTERVAL_SECONDS` guard (default 60s) additionally
refuses to poll again before that many seconds have passed since the
last *successful* poll, regardless of which of the two mechanisms above
asks for it, or how tight a `--interval` is requested.

## Direct answers to each question asked

- **Does a standalone poller process exist?** The *script* exists
  (`scripts/background_poller.py`). Whether a *process running it*
  exists right now is a separate, checkable fact, not a property of the
  code. At the time of this investigation, `Get-Process` showed no
  Python process running that script -- only the Streamlit server for
  `luxor_desk.py` (and, transiently, this session's own test runs).
- **Does Streamlit automatically poll while open?** No. Confirmed by
  reading `dispatch.py` end to end -- the only call to
  `poll_for_new_messages()` is inside the Sync button's click branch.
  No `st.autorefresh`, no background thread, no timer exists anywhere
  in `luxor_desk.py` or the pages it imports.
- **Does ingestion happen only after pressing Sync (for the canonical
  app)?** Yes, for `luxor_desk.py` specifically. The one other way a
  poll can happen at all is a human separately running
  `scripts/background_poller.py`.
- **Does any timer/thread/scheduled task/startup hook/auto-refresh
  trigger ingestion?** No. The only auto-refresh in the whole
  repository (in the older console app) is explicitly local-only and
  never calls Quo, by its own design and comment.
- **What process wrote the recent pending rows?** `quo_poll_state.updated_at`
  advanced twice during this investigation window (most recently to
  `2026-07-31T01:04:54Z`), while no standalone poller process was
  running and the canonical `luxor_desk.py` app was live with a real
  operator (you) logged in and testing the New Jobs page on your phone.
  The most consistent explanation is that this came from Sync being
  tapped in the live app during that session -- not a background
  process, since none was running. I did not click Sync myself (I never
  entered the login password this session), and I am not asserting this
  with certainty beyond what the process/timestamp evidence supports --
  if you didn't tap Sync, that would be worth re-checking.
- **How often can it run?** Sync: as often as a human taps it, subject
  to the 60-second minimum-interval guard shared with the poller
  script. `background_poller.py --loop`: every `--interval` seconds
  (default 300), also subject to that same guard.
- **Does closing/restarting Streamlit stop ingestion?** Yes, completely,
  for the Sync-button path (it has no existence outside a button click
  during a live session). For `background_poller.py`, closing *that
  script's own terminal* stops it; Streamlit's state is irrelevant to
  it since it's a fully separate process with its own `storage.connect()`
  call.

## No behavior changed by this investigation

This document is read-only in intent: no ingestion code, no polling
interval, no Sync button, and no `background_poller.py` behavior was
modified while writing it. It exists purely to record what the two
existing, already-tested mechanisms actually do.
