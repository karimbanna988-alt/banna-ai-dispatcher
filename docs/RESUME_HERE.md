# Resume Here

Written before an overnight break so the next session — a new chat, a
different engineer, or a different AI — can pick this up cold.
Everything below was verified, not assumed.

**Product:** Banna Dispatch · **Tenant:** Luxor Locksmith
**HEAD:** `6bfabf9` · **Tag:** `rc-2026-08-15-correlation-safety`
**Working tree:** clean. All owner untracked files preserved.

---

## 1. What is running (leave it alone unless it breaks)

| Port | What | State when this was written |
|---|---|---|
| 8512 | Console + operator API | 200 |
| 8513 | WhatsApp adapter (a thread inside 8512) | ok |
| 8514 | WhatsApp Shadow Bridge | `paired: true, connected` |

**Passive capture continues while nobody is working.** WhatsApp → 8514 →
8513 → SQLite → 8512. No model call is involved, so observations keep
accumulating overnight with Claude completely idle. Proven this session:
10 new observations landed during a window when no analysis ran, and all
10 appeared in the Live Operations feed.

Leave all three running. To stop or restart, see
`docs/BANNA_DISPATCH_OPERATIONS.md` — do not improvise commands.

## 2. State at the checkpoint

- Observations: **1724** and climbing
- Retry queue: **0**
- DB integrity `ok`, FK violations `0`
- Business tables: jobs 6, events 19, technicians 18, registry 85
- `whatsapp_outgoing_audit`: 74 — unchanged all session, i.e. **0 sends**
- Backup taken: `C:\Users\child\Desktop\ai-dispatcher-db-backups\dispatcher.db.pre-overnight-2026-08-15-*.bak`
  (integrity ok, 0 FK, schema current)

## 3. Tests

Python **1974 passed / 0 failed** · Node **121 / 0**.
Canonical suite is `tests/`, scoped by `pytest.ini`; the root
`conftest.py` production-database tripwire is active on every run.

## 4. Where the product actually stands

**Works and is verified:**
- Phone-first console: three stages, one per viewport, ≥44px targets, no
  horizontal scroll, offline banner, safe-area handling
- Live Operations feed + real health bar (real sources only)
- Read-only Apprentice suggestions (no approval path exists)
- WhatsApp understanding → routing → WHY → confidence → prepared message
- Stale open jobs separated from current work without deletion or status
  mutation

**Not verified, and not claimed:**
- **Owner iPhone acceptance against the current build.** Every item
  needing a real authenticated session on the owner's device is
  NOT OBSERVED. This is the one blocker to calling the LAN pilot ready.
- Service worker runtime behaviour (unavailable in the test browser; also
  inert over LAN http, which is not a secure context)
- PWA install

**Phone URL** — re-read it, it is DHCP and changes:

```bash
python -c "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.connect(('8.8.8.8',80));print('http://%s:8512/'%s.getsockname()[0])"
```

It was `http://192.168.1.105:8512/`. The start script prints it too.

## 5. The thing most likely to be misread tomorrow

**The board is nearly empty, and that is correct.**

- TO DISPATCH shows **0 WhatsApp candidates**. Not a bug: every WhatsApp
  job observed had *already been forwarded* by the dispatcher, usually
  within 10–100 seconds. Work that is already sent must never be offered
  for dispatch again.
- IN PROGRESS shows **0 current + 1 STALE — REVIEW NEEDED** (job #1,
  E5X5N, ~19 days old, still stored and actionable in Admin).
- TO CLOSE shows 0 because no closing candidates exist right now.

Live candidate counts at checkpoint: 0 waiting · 6 already dispatched ·
0 repost · 24 human review · 0 mapping required. Routing verification:
1 MATCH, 0 MISMATCH, 5 UNVERIFIED.

If tomorrow's board looks empty, check these counts before assuming
something broke.

## 6. Group mappings

**32 owner-confirmed** of 85 registered. **Zero active unmapped groups** —
the confirmation queue is empty. 53 unresolved groups are idle/historical
and not blocking anything.

Confirmed this session (batch 4, `scripts/apply_owner_mapping_batch_4.py`):
Ashraf Charlotte Dispatch → Ashraf/Charlotte · Osos nj Closings →
Osos/NJ · Vancouver Closings → Falcon/Vancouver · Luxor Savannah dispatch
→ Savannah with **no technician** (shared workstream) · Trio Dispatch →
`IGNORE` (internal chat).

**Three facts that must not be quietly undone:**
1. **Luxor Savannah dispatch has no technician on purpose.** The group
   maps to an AREA; the current technician comes from routing. Binding a
   person turns shared coverage into permanent ownership.
2. **Tarek's technician record has no destinations on purpose.** That
   empty record is what keeps his Columbia coverage temporary; his
   permanent area is Savannah.
3. **Osos is not Mohamed Osama.** Two different people, both NJ.

## 7. Safety invariants (structural, not policy)

0 WhatsApp sends · 0 Quo writes (GET-only) · no automatic job mutation ·
no automatic reassignment · no guessed mappings · no invented price,
amount, payment method or availability · ambiguous/unmatched →
HUMAN_REVIEW_NEEDED. Candidate and operator projections are pure and
cannot mutate `jobs.status`. The Apprentice has no approval endpoint.

## 8. Rollback points

| Tag | Commit | What it is |
|---|---|---|
| `rc-2026-08-15-correlation-safety` | `6bfabf9` | current |
| `rc-2026-08-15-active-understanding` | `6a6f5f3` | before the correlation repair |
| `rc-2026-08-14-banna-live-ops` | `5e499d9` | Live Operations + health |
| `rc-2026-08-14-phone-pilot` | `3313214` | phone-first console |
| `rc-2026-08-14-owner-demo-2` | `3edb5be` | protected pre-sprint RC (branch `rc2-safety-checkpoint`) |

## 9. Next step — not more engineering

The next action is **the owner opening the console on his iPhone** and
running the 10-step script in `docs/PHONE_DISPATCH_PILOT.md`, step 10
(Wi-Fi off → OFFLINE banner → Wi-Fi on → clears) being the one that
matters most.

Deferred by decision, not forgotten: remote HTTPS (`docs/BANNA_DISPATCH_DEPLOYMENT.md`
Stage 2), PWA install, a display surface for routing verification, and
the two demo group IDs for the isolated owner demo.

## 10. Key documents

`docs/BANNA_DISPATCH_OPERATIONS.md` — start/stop/recovery/backup
`docs/BANNA_DISPATCH_DEPLOYMENT.md` — LAN pilot now, HTTPS next
`docs/PHONE_DISPATCH_PILOT.md` — iPhone acceptance script
`docs/PHONE_DISPATCH_PRODUCT_CONTRACT.md` — what the phone must answer
`docs/PENDING_OWNER_QUESTIONS.md` — open owner-only questions
