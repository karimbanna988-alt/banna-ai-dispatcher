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

## Update -- "Live Shift Shadow Mode" round (owner: Safe is dispatching live; do not interfere)

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

## Exact next task if continuing

**Blocked on the owner.** Do not scan a new QR, and do not attempt
another pairing run, until the owner decides how to proceed. Options
to present: (a) scan a fresh QR now (accepting this device's linked
session needs re-establishing), (b) check the WhatsApp app itself
first (Settings > Linked Devices) to see whether the old session is
still listed as active before deciding, or (c) have the owner
authorize one more careful, deliberately slow, single (non-retried)
pairing attempt to see if that alone reaches "ready" without a QR --
useful mainly to test the "the retries themselves caused this" theory,
but this is still a real live pairing attempt against the account and
needs explicit sign-off first, not silent action.
