# Resume Here

Written before an overnight break so the next session (or a different
engineer, or a different AI) can pick this up cold. Everything below was
verified, not assumed.

**HEAD when this was written:** `3bd458b`
**Working tree:** clean — every change is committed.
**Live stack:** left RUNNING deliberately (see "What is running" below).

---

## 1. What this system is doing right now

A **receive-only WhatsApp shadow dispatcher**. It watches the real
locksmith operation — the human dispatcher **Seif** works the actual
shift — captures every message from the registered WhatsApp groups,
interprets it, correlates it into job traces, and shows a human what it
concluded.

It has never sent a WhatsApp message, never written to Quo, and never
created, closed or modified a real job. That is structural, not
policy: `whatsapp_shadow_bridge/test/no-send-static-check.test.js`
greps the whole bridge source and fails the build if a send-capable
call ever appears.

---

## 2. What is running (leave it alone unless it breaks)

| Port | Service | How it was started |
|---|---|---|
| 8512 | CRM API + UI (`operator_api.py`) | `Start-Process python operator_api.py --port 8512` (detached, minimized) |
| 8513 | WhatsApp adapter | embedded thread inside the 8512 process |
| 8514 | Node WhatsApp bridge | `scripts/start_normal.ps1` in its own window |

Plus three visible monitor windows on the desktop:
`LUXOR - LIVE OBSERVATIONS`, `LUXOR - SHADOW DECISIONS`, and the bridge
window.

**Health check (all three):**
```
curl -s http://127.0.0.1:8512/ -o /dev/null -w "%{http_code}\n"
curl -s http://127.0.0.1:8513/health
curl -s http://127.0.0.1:8514/health
```
Expect `200`, `{"status":"ok"}`, and
`{"enabled":true,"paired":true,"status":"connected"}`.

**If 8514 is down:** restart with `scripts/start_normal.ps1` from
`whatsapp_shadow_bridge/`. It reuses the on-disk session — the session
has now survived three machine reboots and two process restarts with
**zero** QR prompts. Only ask for a new QR if normal reuse genuinely
fails.

**If 8512/8513 are down:** restart `operator_api.py`. The bridge's
durable retry queue protects observations across the gap.

---

## 3. Ground truth: verified numbers

| | |
|---|---|
| WhatsApp observations captured | 526 |
| Outgoing/self audit rows | 74 (frozen — see §5) |
| Registry | 85 groups: **27 owner-confirmed / 58 UNMAPPED_WORK** |
| Jobs / events / technicians | **6 / 17 / 18 — untouched all session** |
| Ticket traces | 113 tickets, **23 multi-group chains** |
| DB integrity | ok, 0 FK violations |
| Python tests | see `.runtime/pytest_overnight.log` |
| Node tests | 118/118 |

Registry counts did **not** move this session. Nothing was auto-mapped.

---

## 4. What was built this session (15 commits, `6e3c3d4..3bd458b`)

**Interpretation fixes — each measured against real traffic first:**
- `0982ffe` — forwarded job blocks were misread as technician ETAs (17
  real instances, 3 groups); closing amounts always claimed "missing"
  even when stated (5 of 8 real closings had an unambiguous
  `$AMOUNT METHOD` shape).
- `81699af` — customer-no-answer had no category (6 instances, 4 groups).
- `076d2d4` — a bare "K" in a company intake group is the dispatcher
  acknowledging, not a broken job posting.
- `00248b4` — **the big one.** The rule was literally "any digit means
  ETA": 105 messages labeled ETA, only **6** actually stated a time
  (~94% wrong). Split into bare @mention (33), price quote (6), and a
  real ETA requiring time language.
- `58f219d` — first-person travel → `TECHNICIAN EN ROUTE`, anchored
  tightly because "She has someone on the way" is the *customer's* other
  locksmith.

**New capability:**
- `f44b6e3` — `src/dispatcher/shadow_trace.py`: reconstructs end-to-end
  job chains from observations alone, correlated by **exact ticket
  only** (timing never establishes a link). Report:
  `python scripts/report_whatsapp_shadow_traces.py`
- `2c85c91` — `scripts/monitor_whatsapp_shadow_decisions_live.py`: live
  terminal view of the actual interpretation decisions.

**Real bugs fixed:**
- `33544e7` — **adapter returned a connection reset instead of 429/401.**
  Rate-limit and auth checks run before the body is read, so an early
  rejection closed the connection with the body unread. I had earlier
  dismissed the resulting flaky test as an environment artifact;
  re-running it isolated failed 1 in 3, which disproved that. A bridge
  hitting a bad secret would have seen an opaque network error instead
  of the reason.
- `8d1d0f8` — I broke the `owns_no_sql` guard with a comment containing
  the literal `"UPDATE "`. Fixed the comment, **not** the guard.

**Data (backed up, integrity-checked before and after):**
- `2ebc236` — linked 4 owner-confirmed groups to their technician record,
  only where mechanically provable (the technician's own record already
  pointed back at that exact group name).

---

## 5. Things a fresh reader will otherwise get wrong

**Everything reads `UNMATCHED` — that is correct, not broken.**
Technician correlation matches against an open job *assigned to that
technician* in the `jobs` table. No WhatsApp job has ever been promoted
into `jobs`, so there is nothing to match. The workflow evidence lives
in the observations themselves — that is exactly why `shadow_trace.py`
exists.

**`whatsapp_outgoing_audit` is frozen at 74 — also correct.**
It only captures `fromMe` messages from the *paired* account (the
owner's). Seif dispatches from a **different** WhatsApp account, so his
actions arrive as **incoming** messages. Not a capture failure.

**The 25-item retry queue is inert legacy.**
All 25 predate the media/empty-body fix (`8e549bd`) and can never
deliver as-is. They do not block current ingestion.
`scripts/quarantine_legacy_whatsapp_retry_observations.py` exists and is
tested, but was **deliberately not run**: the bridge is live and writing
to that same JSON file with no locking, so it should be run during a
planned bridge stop, not while shadowing.

**Don't run `pytest` from the repo root without `--ignore=Exports`** —
an untracked `Exports/` copy of the repo collides on module names.

**The DB tripwire in `conftest.py` will "fail" during a full test run
while shadowing is live.** That is the live capture appending
observations, not a test writing. Verify by checking
jobs/events/technicians are still 6/17/18 — never by weakening the
tripwire.

---

## 6. Open owner questions (all queued, none blocking)

Full detail with evidence in **`docs/PENDING_OWNER_QUESTIONS.md`**:

1. **`Ashraf Charlotte Dispatch`** — most active unmapped group (24 in /
   11 out). Its paired closings group is already confirmed, and ticket
   `JOBW-ZZ3D` was observed flowing between them. But Ashraf's record
   names `'Ashraf Charlotte Dispatch/ Saturday'` — a *different* string,
   and `/Saturday` `/Sunday` suffixes are a real convention here (Joe,
   Ziyadah, Khries all have them). **Left UNMAPPED, unguessed.**
2. **Sender `149245868920864@lid`** — posts in 11 groups and forwarded 22
   job blocks, so it is classified *dispatcher-shaped* on behaviour
   alone. Whether it is Seif's account is **not** asserted.
3. Two company-intake groups naming a technician (routing fact, not
   ownership).
4. **Technician-destination gap — do NOT automate.** Tarek's record is
   deliberately empty; a general backfill would convert his
   owner-declared *temporary* Columbia coverage into a permanent
   assignment. Only Falcon is a genuine gap.
5. Freeform job postings without a ticket block — 2–3 instances only,
   too thin and too risky to parse.

---

## 7. Where the shift stood

All four workflow segments have been observed in real traffic:
company intake → dispatcher forward (14 chains), dispatch → closing (4),
technician closing → company closing (1). **No single ticket has
traversed all four yet**, and the reason is measurable: those 14
forwards happened 14–49 minutes before the check, so the jobs were
still in flight. A complete four-stage chain should form naturally as
the operation continues.

To check:
```
python scripts/report_whatsapp_shadow_traces.py
```

---

## 8. Suggested next steps

1. Answer the Ashraf mapping question — it is the single highest-value
   unblock and cannot be resolved without the owner.
2. Check whether a complete four-stage chain has formed overnight.
3. Quarantine the 25 legacy retry items during a planned bridge stop.
4. Keep the observe → measure → test → fix → commit loop going. The bar
   held all session: a pattern needs repeated evidence across multiple
   groups before it becomes a rule; group-name similarity is never
   sufficient for a mapping.
