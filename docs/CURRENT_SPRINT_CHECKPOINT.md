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

## Current live state at checkpoint time

- CRM (8512) + Python adapter (8513): PID 18140, restarted once this
  sprint to load code (Node bridge untouched throughout, PID 15844,
  resumed the existing paired session -- no QR needed).
- Registry: 85 groups (15 owner-confirmed, 70 `UNMAPPED_WORK`).
- jobs=6, events=10, unchanged all sprint; integrity OK; 0 FK
  violations.
- 3 read-only PowerShell monitoring windows open (bridge log tail, CRM
  log tail, DB observation poll) -- owner-requested visibility, no
  restart involved in opening them.

## Explicitly not done (disclosed, not silently skipped)

- No correlation example with a real STRONG/EXACT match has been
  naturally observed yet -- none of the 6 real jobs are currently
  assigned to a confirmed-registry technician (Sebai/Toto/Joe/Ashraf/
  Kabaka). The logic is unit-tested and proven correct; a live example
  needs either real dispatch activity or an owner-assigned job to
  appear naturally.
- `COMPANY_CLOSINGS` interpretation path is unit-tested but hasn't
  naturally appeared in the live feed window yet.
- No dedicated outgoing-audit UI view -- that data is captured and
  queryable (`whatsapp_outgoing_audit`) but not yet surfaced as its own
  screen.
- Toto and Joe are real, owner-confirmed technicians but are not yet
  rows in the `technicians` table -- their registry entries carry
  `technician_name_unconfirmed` instead of a real `technician_id`, so
  they can never match an open job (always UNMATCHED) until added.

## Remaining owner decisions (see `docs/WHATSAPP_ROUTING_MAP_REVIEW.md` section 6 for the full list)

1. Add Toto and Joe as real `technicians` rows (or confirm they're
   aliases of existing Greenville technicians Mohamed Shabaan/Kimo/
   Youssef Sweillam).
2. Confirm/reject the 70 `UNMAPPED_WORK` groups' offline-guessed
   company/technician identities (Adam-Charlotte-NC, Adam-Asheville-NC,
   Beeko/Bekoo, Joy Locksmith, Osos, Hashem, Ahmed, Bassam, Farouk, and
   all the plain technician-named dispatch/closing groups not yet
   confirmed).
3. Where AGN/Main Leads/Lucky intake actually arrives (no WhatsApp
   group name matched any of them except AGN's closing group).

## Exact next task if continuing

Nothing blocking remains for continued safe engineering work. Highest-
value next steps, in order: (a) get an owner decision on Toto/Joe so
TECH_DISPATCH/TECH_CLOSINGS correlation can produce a real STRONG/EXACT
match live; (b) confirm/reject more `UNMAPPED_WORK` groups to shrink
that 70-group backlog; (c) a dedicated outgoing-audit view if the owner
wants to review the dispatcher's own sent messages in-CRM.
