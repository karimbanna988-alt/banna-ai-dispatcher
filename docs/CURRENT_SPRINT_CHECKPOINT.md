# Current Sprint Checkpoint — Multi-Group WhatsApp Shadow Dispatcher

Supersedes the previous checkpoint (Operator Speed Round 2 -- preserved
in git history, HEAD `b28ee34`). This sprint took the system from a
single-group (Sebai/Augusta) WhatsApp pilot to a live, multi-group
receive-only shadow dispatcher with a real interpretation layer, still
running as this checkpoint is written.

## Starting state

HEAD `b28ee34`. Real DB: 6 jobs, 10 events, 15 technicians, integrity
OK. WhatsApp shadow bridge paired to a single group (Sebai Augusta
Dispatch) only; Python adapter never started; no group registry
existed.

## What shipped (in commit order)

1. **`0889f2a`** -- Fixed Action Center showing 238-359h-old raw
   `quo_pending_messages` as current work. Root cause:
   `get_live_dispatch_queue()`'s "live" bucket is bounded by a one-time
   activation boundary, not a rolling window; `_compute_action_center`
   never applied the recency filter `route_new_jobs` already had. Added
   `ACTION_CENTER_MESSAGE_LOOKBACK_MINUTES` (24h), applied only to
   pre-commit messages -- committed jobs stay governed by status alone
   (proven via job id=1, a real 359h-old `Approved`/unassigned job that
   correctly keeps surfacing).
2. **`c1a8cef`** -- `whatsapp_group_registry` + `whatsapp_outgoing_audit`
   tables (optional-capability pattern, same as Shadow Mode -- see
   `whatsapp_registry_schema_present()`). Seed/sync/quarantine scripts.
   13 cross-group/cross-technician safety tests.
3. **`d4e0c51`** -- Outgoing/self message audit capture in the Node
   bridge (`message_create` listener, `fromMe`-only, structurally
   separate table from the actionable queue).
4. **`abd2742`** -- Registry role/mapping-status shown in Inbox Review.
5. **`5f8fe9d`** -- Offline group-inventory and routing-map-review docs
   (`docs/WHATSAPP_GROUP_INVENTORY.md`, `docs/WHATSAPP_ROUTING_MAP_REVIEW.md`).
6. **`a7d3031`** -- The interpretation layer:
   `route_whatsapp_shadow_feed` enriches CONFIRMED-registry observations
   by reusing existing logic (`extract_job`/`classify_incoming` for
   intake; a generalized `technician_ack` EXACT/STRONG/AMBIGUOUS/UNMATCHED
   vocabulary for technician-group correlation). `route_whatsapp_promote_to_job`
   reuses the exact existing job-commit path
   (`DispatcherService.confirm_whatsapp_dispatch`), keyed by the WhatsApp
   message_id for idempotency. Inbox Review's WhatsApp section now shows
   time/role/mapping-status/company-technician/interpretation/proposed-job/
   confidence/next-action, polling every 4s. 19 new tests.

## Live-verified in the browser (real traffic, not fabricated)

- `GMI Adams job receiving` -> CONFIRMED / COMPANY JOB INTAKE ->
  correctly extracted nothing usable from freeform WhatsApp text ->
  `missing: ticket, phone, address, service` -> `REVIEW` (honest, not
  fabricated).
- `Sebai Augusta Dispatch` -> CONFIRMED / TECH DISPATCH -> `TECHNICIAN
  UPDATE` -> `UNMATCHED` (correctly -- no open job is currently assigned
  to Sebai in the real jobs table) -> `HUMAN_REVIEW_NEEDED`, Attach
  button present.
- `Joe Greenville Closings` -> CONFIRMED / TECH CLOSINGS -> `CLOSING
  RECEIVED` -> `missing: amount, payment_method` (never invented).
- Dozens of `UNMAPPED_WORK` groups (Vancouver Dispatch, Ashraf Charlotte
  Dispatch, Kimo Alabama Dispatch, etc.) correctly showing
  `GROUP_MAPPING_REQUIRED`, non-interpreted, non-mutating.
- Live 4s polling confirmed working (list visibly refreshed mid-scroll).
- No console errors after login. `route_whatsapp_shadow_feed` latency:
  ~11ms for 100 items.

## Update -- "Live Shift Shadow Mode" round (owner: Seif is dispatching live; do not interfere)

Owner mapping batches 2 (`1847778`) and 3 (`83f7fbd`) landed since the
section above was written, plus Toto/Joe added as real technicians
(`3014fb4`) and two correlation-labeling bug fixes (`7c2d5c9`,
`a4911a5`). Registry is now **27 owner-confirmed / 58 `UNMAPPED_WORK`**
(not 15/70 as stated above -- that count is stale, kept for history).

**Real finding this round: the entire shadow stack was down for ~15.4
hours before this round started** (last captured observation
2026-08-11 03:05 UTC / 06:05 local; discovered at ~18:29 UTC). No
process (CRM, adapter, or bridge) was running. Root cause unknown --
not something this round's code changes caused (they hadn't been made
yet). Real live-shift WhatsApp traffic during that ~15.4h window was
**not captured**. CRM+adapter restarted cleanly (fresh `technician_id`/
registry data loads live, no code change needed there). The bridge
(8514) would not reconnect: two consecutive crashes with an identical
stack (`ProtocolError: Runtime.callFunctionOn -- Execution context was
destroyed` inside whatsapp-web.js's `Client.initialize` -> `Client.inject`),
before ever reaching the QR or ready event -- so this says nothing
about session validity, only that Puppeteer/Chromium page injection is
failing. Cleared `.wwebjs_cache/` (whatsapp-web.js's cached copy of
WhatsApp's own web-client JS bundle -- NOT the LocalAuth session/pairing
data, which lives separately in `.runtime/whatsapp-shadow-session/session/`
and was never touched) as the standard first mitigation for this exact
error class, then retried. See "Current live state" below for the
outcome of that retry -- if it's still failing, this is a real blocker
that may need either a `whatsapp-web.js` version bump (a real dependency
change, not attempted without flagging it first) or owner awareness
that shadow capture has a gap.

**Fixed this round (`whatsapp_shadow_bridge`, no DB change):** a media
message with no caption (photo, voice note, sticker, etc.) arrived with
`msg.body === ""`, which could never satisfy `adapterClient.js`'s
"text is required" validation -- so it failed locally on every retry,
forever, and piled up unrecoverably in the retry queue (confirmed: 25
stuck items, 100% of the real queue, all empty-text). Fixed by
capturing `msg.type`/`msg.hasMedia` in `client.js` and, only when real
text is empty, substituting an honest, generic, metadata-only label
(e.g. `[media message, no caption -- type: image]`) in
`messageHandler.js` -- never invents what the media contains, never
overrides real caption text. Applied to both the incoming pipeline and
the outgoing/self-message audit pipeline. 7 new Node tests (24/24 in
`messageHandler.test.js`, 104/104 across the full Node suite). Full
Python suite: 1636/1639 passing (3 failures were a self-inflicted
concurrent-process port collision + one pre-existing flaky rate-limit
test, both confirmed to pass cleanly in isolation -- not caused by this
change).

**Fresh mapping-evidence pass (read-only, before the outage was found):**
re-checked all 58 `UNMAPPED_WORK` groups against real accumulated
traffic (251 pending observations, 74 outgoing-audit rows at that
point). Zero exact `dispatch_group`/`closing_group` name matches (fully
deterministic check). Only two unmapped groups have any real message
traffic at all: **Ashraf Charlotte Dispatch** (12 messages -- K/ETA/
no-answer/price-negotiation content, classic TECH_DISPATCH shape, but
sender is an opaque WhatsApp `@lid` id with no self-identifying name in
any message text, and no ticket number ties it to a specific job) and
**Tarek Savannah Dispatch** (3 messages, same limitation). Both remain
STRONG_PROPOSAL_NEEDS_OWNER, not auto-confirmed -- name match plus
content shape is exactly the "not enough" bar this session has held to
throughout (a single sender identity or content shape is not proof).
Tarek Savannah Dispatch is notable: it's plausibly Tarek's *permanent*
home-area group (distinct from the already-confirmed *temporary*
Columbia coverage group) -- still owner-confirmation-only, per the
explicit instruction not to guess this exact distinction.

## Current live state at checkpoint time

- CRM (8512) + Python adapter (8513): restarted, both responding
  (`/health` on 8513 returns `{"status":"ok"}`; CRM root returns 200).
- Bridge (8514): see outage/crash finding above -- status as of the
  last retry attempt should be checked in the live PowerShell/log
  windows, not assumed from this doc.
- Registry: 85 groups total, 27 owner-confirmed, 58 `UNMAPPED_WORK`.
- jobs=6, events=17; DB untouched by this round's diagnostic queries
  (all read-only).

## Explicitly not done (disclosed, not silently skipped)

- The full "shadow evidence/trace-correlation learning system" asked
  for in the "Live Shift Shadow Mode" brief (persistent SHADOW TRACE
  read-model with EXACT/STRONG/AMBIGUOUS/UNMATCHED chain confidence,
  continuous 5-category A-E evidence table, deterministic
  pattern-learning from repeated message formats) was **not built** this
  round. That is genuinely multi-session scope, and the same brief
  explicitly says not to build new dashboards/frameworks -- building it
  well would mean a new persistent schema and read-model, which is a
  bigger step than "keep observing and fix bugs as you find them." What
  *was* done instead: the concrete, well-defined, high-value bug found
  during observation (media/empty-body retry churn) was fixed with
  tests, and a fresh no-guessing evidence pass was run across all 58
  unmapped groups.
- No dedicated outgoing-audit UI view (unchanged from before).
- Toto and Joe are now real `technicians` rows (this is no longer
  outstanding -- corrects the stale note directly above).

## Remaining owner decisions

1. Ashraf Charlotte Dispatch and Tarek Savannah Dispatch (see evidence
   above) -- strong name/content-shape candidates, not deterministically
   confirmable from message content alone.
2. The rest of the 58 `UNMAPPED_WORK` groups with no traffic yet --
   nothing to evidence until they produce real messages.
3. Whether the ~15.4h stack outage this round found needs any owner
   action (e.g. was this a deliberate stop, a machine restart, or an
   unexpected crash the owner should know about independently of this
   session).

## Update -- pairing-crash root cause found and fixed, but a new QR is now required (`b28b042`)

Root cause of the repeated `ProtocolError: Execution context was
destroyed` crash: `pairing.js` called `client.initialize()` with
**no `.catch()` at all** -- an unhandled promise rejection, which
crashes the whole Node process by default (this is why the health
server died too, even though it has no real dependency on WhatsApp
pairing). Fixed with a bounded in-process retry (3 attempts, fresh
`Client` instance each time, `client.destroy()` cleans up the crashed
browser only -- never touches on-disk session files, never calls
`.logout()`) so a transient injection race no longer takes the whole
bridge down. 12 new tests in `test/pairing.test.js` (previously zero
coverage on this file) plus a regression test in `index.test.js`
proving `getGroups` uses the client that actually became ready, not a
stale reference -- a real bug the retry design would otherwise have
reintroduced. 118/118 Node tests pass.

**Live-tested against the real paired session, per explicit
authorization to restart 8514 as needed.** The fix worked exactly as
designed: attempt 1 crashed (`Execution context was destroyed`),
caught and retried; attempt 2 crashed again (`...most likely because
of a navigation`), caught and retried; attempt 3 did **not** crash --
but LocalAuth decided the existing session was no longer usable and
requested a **new QR code**. Per the explicit safety rule ("if a QR
becomes unavoidable, STOP and tell the owner exactly what to scan"),
stopped immediately: did not scan/approve/simulate anything, cleanly
killed the pending pairing process (`node.exe` + its Chromium child),
verified no orphaned processes remain, and left the on-disk session
directory completely untouched.

Working theory (not confirmed): the two abrupt browser kills, both
happening mid-navigation during WhatsApp Web's own session-restore
bootstrap, may have themselves contributed to the session degrading by
the 3rd attempt -- i.e. the retry fix's own corrective action might be
what tipped this particular session into needing re-auth, rather than
the session being independently dead before I touched it. This is a
real, disclosed uncertainty, not a confirmed root cause -- there's no
way to know from the logs alone whether the same QR requirement would
have appeared on a single careful un-retried attempt too.

## Current live state at checkpoint time

- CRM (8512) + Python adapter (8513): running, healthy.
- Bridge (8514): **stopped** (not paired, not attempting to reconnect)
  -- deliberately halted after the QR appeared, awaiting owner
  direction. Session files at
  `.runtime/whatsapp-shadow-session/session/` are untouched.
- HEAD: `b28b042`.

## Update -- RESOLVED: re-paired, and the session now survives a full reboot

The owner authorized and completed a fresh QR pairing. Outcome, verified:

- Paired successfully via `scripts/start_pairing.ps1` (took 9 QR
  refreshes before the owner reached it; each refresh was re-rendered
  and re-verified as genuinely scannable against an independent QR
  decoder before being shown, and the raw payload was never logged).
- **Controlled process-restart test: PASSED** -- stopped the bridge,
  restarted via `scripts/start_normal.ps1`, reconnected straight to
  `paired:true` with zero QR prompts.
- **Unplanned full-laptop-reboot test: PASSED** -- the machine restarted
  on its own; on restart the bridge reconnected from the on-disk session
  with zero QR prompts. This is a materially stronger durability result
  than the process-restart test and supersedes the earlier open question
  about session fragility.
- Post-reboot DB check: `integrity_check` = ok, 0 FK violations, no
  leftover WAL/journal files -- the unclean shutdown caused no damage.

The earlier "did our own retries degrade the session?" theory remains
**unresolved but no longer blocking** -- the session has now survived
two clean restarts and one hard reboot without incident.

## Update -- live shadow round: three interpretation gaps found and fixed

All three were found by reading real captured traffic, and in each case
the pattern was counted across the corpus BEFORE any code was written --
never generalized from a single example.

1. **`0982ffe` -- forwarded job blocks misread as technician updates.**
   A dispatcher forwarding the full job posting into a technician's
   dispatch group was classified as a "TECHNICIAN ETA/UPDATE", because
   that branch only expected short replies and its "any digit means ETA"
   heuristic swallowed the ticket block. Evidence: 17 such observations
   across 3 different confirmed dispatch groups/companies. Now labeled
   as the SEND_JOB_TO_TECHNICIAN step and correlated to the existing job
   by exact ticket number. Live count after the fix: 23 occurrences
   correctly labeled.
2. **`0982ffe` -- closing amounts always reported missing.** TECH_CLOSINGS
   hardcoded `missing_fields = ["amount","payment_method"]` for every
   message, even ones plainly stating both. Evidence: 5 of 8 real
   closing messages carried an unambiguous adjacent `$AMOUNT METHOD`
   shape. Now parsed deterministically. Live result: 5 extracted, 19
   still honestly reported as missing -- never invented.
3. **`81699af` -- customer-no-answer had no category.** Operationally
   distinct from decline/ETA/update: the job is stalling and the company
   needs telling. Evidence: 6 genuine standalone instances across 4
   groups and 4 technicians. Two ordering subtleties the real data
   forced: it must be checked before the digit heuristic (a real message
   contained "...after I drive 1 hour"), and a leading "No" must not be
   read as the bare `no` decline token ("No answer sent a text" is a
   report, not a refusal).

Also added `scripts/monitor_whatsapp_shadow_decisions_live.py`
(`2c85c91`) -- a read-only terminal view of the live interpretation
decisions, calling the same `_interpret_whatsapp_observation()` the CRM
uses so the two can never drift.

## Current live state at checkpoint time

- CRM (8512) + Python adapter (8513): **running, healthy**.
- Bridge (8514): **running, `paired:true, status:connected`**.
- HEAD: `81699af`.
- Registry: 85 groups, 27 confirmed, 58 `UNMAPPED_WORK` (unchanged --
  no mapping was auto-confirmed this round; see below).
- Business truth untouched all round: jobs=6, events=17, technicians=18.
- Live acceptance re-verified: 0 duplicate message_ids, 0 outgoing/self
  messages leaked into the inbound actionable queue, all unmapped-group
  observations still `GROUP_MAPPING_REQUIRED`, media/no-body messages
  captured as metadata instead of retrying forever.

## Exact next task if continuing

Keep shadowing. No blocker. Open items, in priority order:

1. Continue the observe -> evidence -> test -> fix -> commit loop on
   live traffic as it arrives.
2. Two unmapped groups now carry real traffic and remain the strongest
   mapping candidates, still **owner-decision-only** (name similarity
   plus content shape is deliberately not sufficient): `Ashraf Charlotte
   Dispatch` and `Tarek Savannah Dispatch`.
3. ~25 legacy empty-body items sit in the retry queue from before the
   media fix. They are inert (not blocking current ingestion) and were
   deliberately left alone this round: the bridge is live and writing to
   that same JSON file with no locking, so running the quarantine script
   concurrently risks racing real in-flight messages. Do it during a
   planned bridge stop, not while shadowing.
