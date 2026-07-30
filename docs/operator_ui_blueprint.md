# Operator UI Blueprint

Factual design spec for the Phase 4 UI rebuild. Business logic, storage,
and DispatcherService are unchanged -- this document describes
presentation and wording only.

## What was wrong before

- No shared header: each page just showed its own title; no visible
  Quo connection state, sync time, or logout control anywhere.
- New Jobs workspace led with raw incoming text and internal field
  labels before the dispatcher could see what mattered.
- Active Jobs mixed the technician-status reply and the Commercial
  Update workflow inside overlapping expanders in an order that didn't
  match how a dispatcher actually works a job.
- Commercial Update showed raw enum values (`FIXED`, `WILL_CALL_BACK`)
  and a numeric confidence score directly in the workflow.
- History/Activity showed timestamps and event labels with no way to
  search, and Activity duplicated History's job-level detail without
  being clearly positioned as the secondary/audit view.
- Technicians was a flat list missing closing destination and vehicle
  restrictions.
- Shadow Review looked like a debugger: episode IDs and JSON-shaped
  labels up front, no explanation of what the page is for.

## Global navigation

Order (unchanged from the existing app, confirmed already correct):
New Jobs -> Active Jobs -> Closings -> History -> Technicians ->
Activity -> Shadow Review.

## Global header

One `render_header(conn, title)` component, called at the top of every
page://
- `LUXOR LOCKSMITH` (fixed)
- current page title
- Quo connection dot + label (`Connected` / `Delayed` / `Disconnected`)
- last successful sync time (human-relative)
- `Sync` button (New Jobs page only -- syncing is only meaningful there)
- `Log out` control

No PIDs, schema revision, DB path, git hash, or env vars in the header.
Those move under a page-level `Technical details` expander where a page
already has diagnostic content worth keeping (New Jobs' routing
internals, Active Jobs' extraction confidence, Shadow Review's episode
ID).

## Page-by-page

### New Jobs
Card order: source + ticket + time received -> city/state -> service
-> wait-time/vehicle when present -> review warning when present ->
recommended technician + WhatsApp destination -> action row. Empty
fields are omitted, never shown as blank. Original message and
extracted-field dump move under `Original message` / `Technical
details`.

Actions (AGN/Lucky): `Copy K` (st.code, native copy) -> `I sent K in
Quo` -> `Copy technician message` -> `I sent it in WhatsApp` -> (once
opened) `Confirm dispatched`. Main Leads skips the two K steps. A job
is not created until `Confirm dispatched` is clicked -- unchanged
business rule, now labeled unambiguously.

### Active Jobs
Section order: Current job summary -> Technician update -> Commercial
update -> Company response -> Pass / Cancel -> Closing readiness ->
Job timeline. "Company response" for a technician-status update lives
directly under Technician update (it's the same underlying
`prepare_company_update`/`confirm_company_update_sent` pair); the
Commercial Update workflow keeps its own separate prepared-response
display per the existing architecture boundary (see
`docs/SHADOW_MODE.md`).

Commercial Update fields use a plain-English label map (`FIXED` ->
"Fixed price", `WILL_CALL_BACK` -> "Will call back", etc. -- see
`luxor_desk_shared.py::COMMERCIAL_*_LABELS`). Ambiguous extraction
shows `Please review this update before saving.` plus one sentence
naming the uncertainty, never a raw confidence float in the main view.

Job timeline replaces raw event rows with the same human-readable
label function History already uses.

### Closings
Five-step visible flow: review closing details -> `Copy company
closing` -> (dispatcher sends manually) -> `I sent this closing
manually` -> `Complete job` (disabled until the closing send is
confirmed -- unchanged rule). Quote amount (from Shadow Mode, if
recorded) and closing amount are shown as two separate lines, never
merged.

### History
Renamed from "Completed / History" to "History" (matches the nav
list). Added client-side filters over already-fetched data: ticket
text search, source, technician, status -- no new query, no new
schema. Each result stays a compact one-line summary; full timeline
stays under the existing "View details".

### Technicians
Card view: name + active/inactive -> areas -> skills -> vehicle
restrictions -> dispatch destination -> closing destination -> a
visible warning chip when either destination is missing. No technician
configuration changes in this phase.

### Activity
Same human-readable label function as History. Explicitly captioned as
a secondary, cross-job audit feed (History is where a dispatcher looks
for one job's story).

### Shadow Review
Opens with one sentence: "Shadow Review compares what the system
suggested with what the dispatcher actually did." Each episode shows,
in order: what was received -> what the system suggested -> what the
dispatcher confirmed or corrected -> what was actually sent -> what
happened afterward -> review decision. Episode ID and raw JSON payloads
move under `Technical details`.

## Empty states (exact wording)

- New Jobs: `No new jobs need attention.`
- Active Jobs: `No active jobs.`
- Closings: `No closings are waiting.`
- History: `No completed jobs yet.` / when filtered: `No jobs match these filters.`
- Technicians: (roster is never empty in practice; unchanged if it is)
- Activity: `No activity recorded yet.`
- Shadow Review: `No dispatcher examples have been recorded yet.`

## Error states

Existing pattern (`This job no longer exists.`, `Paste the technician's
actual reply first.`, schema-upgrade notices) is already free of raw
tracebacks -- kept, extended to any new empty/error path introduced
this phase. No user-facing message ever shows a Python exception.

## Responsive behavior

The existing global CSS already enforces `overflow-x: hidden` on
`html`/`body`/`.block-container`, a two-column desktop grid that
collapses to one column below 900px, and flex-based action rows that
shrink instead of overflow. This phase reuses those same primitives for
every new section (no new raw-HTML width rules introduced) and verifies
structure/content via AppTest. **Live pixel verification at
1440x900/1366x768/390x844 was not performed** -- this environment has
no supported way to point a second live browser session at a
non-production database without touching `storage.DB_PATH`, which was
out of scope for this phase. Human visual inspection after login is
still recommended (see final report).

## Technical details convention

Any page that previously showed parser internals, confidence scores,
raw event payloads, or episode IDs keeps that information, but only
inside a clearly labeled `Technical details` expander, collapsed by
default.
