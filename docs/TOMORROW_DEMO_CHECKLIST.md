# Tomorrow's Demo Checklist

Everything needed to run the owner demo, in one place. Verified against
the isolated demo stack during the pre-demo acceptance check.

## Start (run this shortly before the demo, not hours ahead)

```bash
powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1
```

Re-seeds fresh synthetic data every run and starts the demo CRM on
`http://127.0.0.1:8517`. Takes a few seconds. **Run it close to demo
time** — two of the KPI cards (New, Waiting) are "last 60 minutes"
windows tied to real wall-clock time and will read 0 if the seed is
more than an hour old (see "Expected synthetic KPI values" below).

## URL

`http://127.0.0.1:8517`

## Login procedure (without exposing the password)

1. Open the URL above.
2. You'll see a red **DEMO MODE** banner above the login card before
   you log in at all.
3. Enter the same password you use for the real CRM
   (`LUXOR_DEMO_PASSWORD` in `.env` — look it up there if you don't
   have it memorized; never read it aloud or screen-share the `.env`
   file itself).
4. Click **Log in**.

## Expected DEMO MODE indicator

- **Before login**: a red banner reading "DEMO MODE — Synthetic data
  only. No live Quo or WhatsApp connection." above the login card.
- **After login**: a red banner reading "DEMO MODE — synthetic data
  only, not connected to Quo or WhatsApp" across the top of the app,
  and the header subtitle (next to the LUXOR logo) reads "Demo --
  synthetic data only, not connected to Quo or WhatsApp" instead of
  the real CRM's "Connected -- real data · manual WhatsApp bridge".

If you ever see the app without a red banner while `start_demo.ps1`'s
window says it started successfully, stop and re-check you're on port
8517, not 8512 (the real CRM).

## Ten-minute presentation order

Matches `docs/SALES_DEMO_SCRIPT.md` — full script and "what not to
claim" list live there; short version:

1. **Login** — point out the DEMO MODE banner, mention it's the same
   login every dispatcher on the team uses today.
2. **Command Center** — six KPI cards, Attention Now, Active jobs
   preview, three charts.
3. **Attention Now** — open job **F500** (Main Leads, backdated ~5
   hours) — "no update in over the stale-job target."
4. **Active job** — open **F400** (AGN, Demo Ashraf) — "Completed"
   technician update already in, closing not yet recorded.
5. **Technician update** — show F200's recent ETA update flowing to
   the Active board without retyping.
6. **Closing** — walk F400 through recording a closing.
7. **Audit history** — open the pre-closed fixture (ticket **D300**,
   $95 cash, Demo Sebai) in Completed and show its full timeline.
8. **KPI impact** — back to Command Center, point out Verified Closed
   Amount and Completed Today reflecting what you just did.

## Expected synthetic KPI values (verified this round, fresh seed)

| KPI | Value | Notes |
|---|---|---|
| New (last 60 min) | 0–1 | Decays to 0 as time passes since seeding — reseed close to demo time |
| Waiting for assignment | 0–1 | Same time-decay caveat |
| Active with technician | 5 | F100–F500 |
| In progress | 4 | |
| Closings due | 5 | Includes jobs missing a technician-confirmed closing |
| Completed today | 1 | The pre-closed $95 fixture |
| Verified closed amount today | **$95.00** | 1 of 1 closed jobs has a reliable amount |
| Attention Now | 2 | F500 and D400, both "stale active job" |
| Job source mix | AGN 7 / Main Leads 3 | |
| Job pipeline | Approved/Assigned 4, In Progress 4, Closed 1, Cancelled 1 | |

If Verified Closed Amount doesn't read $95.00, the demo database
didn't seed correctly — see "fallback if demo seed fails" below.

## Fallback: port conflict

If `start_demo.ps1` says something is already listening on 8517:

```bash
powershell -ExecutionPolicy Bypass -File scripts\stop_demo.ps1
powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1
```

If that doesn't clear it, check what's actually on that port before
force-closing anything you don't recognize:

```bash
powershell -Command "Get-NetTCPConnection -LocalPort 8517 -State Listen"
```

## Fallback: authentication fails

- Confirm you're using the CRM's current `LUXOR_DEMO_PASSWORD` — the
  demo reuses it, it is not a separate credential.
- If login still fails, restart the demo (`stop_demo.ps1` then
  `start_demo.ps1`) — a stale process from an earlier session can
  occasionally hold the port with different environment variables.
- As a last resort, run `python tools/system_readiness.py` — it will
  report whether `LUXOR_DEMO_PASSWORD` is present and non-empty
  (never the value) in the current environment.

## Fallback: demo seed fails

`start_demo.ps1` prints `[FAIL] Seeding the demo database failed` and
stops before starting the server if this happens. If you see that:

1. Check the printed Python error in that window.
2. Delete the stale demo database and retry once:
   ```bash
   powershell -Command "Remove-Item .runtime\demo\demo.db -Force -ErrorAction SilentlyContinue"
   powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1
   ```
3. If it fails a second time, do not attempt to debug live in front of
   the owner — fall back to walking through `docs/COMMERCIAL_READINESS.md`
   and `docs/SALES_DEMO_SCRIPT.md` verbally instead, and debug afterward.

## How to verify production DB isolation (do this once before the demo)

```bash
python tools/system_readiness.py
```

Confirms the real database's integrity independently of the demo. To
directly confirm the demo is reading its own database, not the real
one:

```bash
powershell -Command "(Get-Item .runtime\demo\demo.db).Length"
powershell -Command "(Get-Item data\dispatcher.db).LastWriteTime"
```

The second command's timestamp should not change before/after running
the demo — if it does, stop immediately and investigate before
continuing (this would mean the demo is not isolated, which should be
structurally impossible per `tests/test_demo_isolation.py`).

## How to stop all demo processes

```bash
powershell -ExecutionPolicy Bypass -File scripts\stop_demo.ps1
```

Verify nothing is left:

```bash
powershell -Command "Get-NetTCPConnection -LocalPort 8517 -State Listen -ErrorAction SilentlyContinue"
```

No output means clean. This never touches the real CRM (port 8512),
the WhatsApp bridge, or `data\dispatcher.db`.

## Claims we must not make

- **No official WhatsApp integration.** The bridge is an unofficial
  library, receive-only. Never say "WhatsApp Business API" or imply
  Meta/WhatsApp sanctioned this.
- **No fully autonomous dispatch.** Every job requires human
  approve/edit/reject before it's real. AI only extracts fields.
- **No guaranteed revenue or ROI.** No pilot has produced these
  numbers yet — see `docs/COMMERCIAL_READINESS.md` category 21.
- **No "enterprise production ready" claim.** This is local-deployment,
  pilot-scale, single-shared-login software today.
- **No proven ROI without pilot evidence.** Don't cite percentages or
  time-savings that were never measured.
- **No automatic technician-availability knowledge.** The system does
  not know if a technician is actually free — a human still assigns.
- **No unsupported "en route" detection.** The pipeline chart's own
  footnote already discloses this: "En route" has no signal separate
  from "In Progress" in the real data.

See `docs/SALES_DEMO_SCRIPT.md` for the full "what NOT to claim" list
and `docs/KNOWN_LIMITATIONS.md` for the complete, current limitations
inventory.
