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

## Mobile Field Operations Addendum (Phase 4.1)

Phase 4 fixed *what the CRM looks like*. This phase fixes *how fast a
person can act on it* -- specifically a dispatcher working from a phone
and a technician who never opens the CRM at all. No new page, no new
framework, no new business rule: same `DispatcherService` calls, same
storage functions, same widget keys wherever a key was already tested.

### Dispatcher mobile workflow

The dispatcher is assumed to be on a phone as often as a desktop,
switching constantly between this app, Quo, and WhatsApp. The rule
applied everywhere: **one job -> one current stage -> one obvious
primary action.** Concretely:

- New Jobs' K-acknowledgment and WhatsApp-dispatch steps never show two
  `type="primary"` buttons at once. Once "I sent it in WhatsApp" has
  been tapped, that button is replaced by a `✓ Sent in WhatsApp` line
  and "Confirm dispatched" becomes the sole primary action -- previously
  both buttons stayed visible together.
- Active Jobs gained a single `Next: ...` sentence at the top of the
  workspace (derived only from data already computed today: whether a
  technician update exists, its type, and whether a reply for that
  update has already been marked sent) so the dispatcher never has to
  scan the whole page to find the current step.
- Once a step is done, its form collapses into a short `✓ ...` summary
  line instead of staying open and full of empty inputs. The form
  reopens in a small expander (expanded by default only while nothing
  has been recorded yet) so a *later* technician update can still be
  pasted in -- technician updates repeat over a job's life, so
  "completed" here means "collapsed and quick to reopen," never
  "removed."

### Technician communication constraints

Technicians are not CRM users. They read a prepared WhatsApp message
one-handed, in a truck, and reply the same way. This phase did not
change the prepared-message *content* -- the New Jobs technician
message and the Company response text already came from the existing
`DispatcherService`/`updates` formatters, already short, already free
of internal branding. What changed is how the dispatcher captures the
technician's reply: one obvious `Technician's actual reply (paste
exactly)` field per section, always the same shape, never a multi-field
form that assumes the technician wrote a structured report.

### One-hand / tap-target rules applied

- Every button already used `width="stretch"`; a new global rule sets
  `min-height: 44px` on every Streamlit button so tap targets meet the
  44-48px target without hunting down each individual button.
- A new `max-width: 480px` media rule forces every `st.columns()`-based
  action row (K-copy, commercial range low/high, active-job action
  pairs, closing action pairs) to stack to one column on a phone-sized
  viewport, instead of relying on Streamlit's own column-wrap
  threshold. The two-panel desktop `desk_layout` CSS grid (queue +
  workspace) already collapsed to one column under 900px in Phase 4 and
  is unchanged.
- Secondary actions (Pass/Cancel, Technical details, "paste another
  update") stay inside `st.expander`, visually quieter than the primary
  action buttons above them.

### Sequential action rules (exact button sequences)

- AGN/Lucky New Jobs: Review (the always-visible card header/meta/tech
  line -- see below) -> Copy K -> I sent K in Quo -> Copy technician
  message -> I sent it in WhatsApp -> Confirm dispatched.
- Main Leads New Jobs: Review -> Copy technician message -> I sent it
  in WhatsApp -> Confirm dispatched (no K step, unchanged from Phase 4).
- Commercial Update: Paste technician message -> review the detected
  fields -> correct only what's wrong -> Save update -> Copy company
  response -> Send manually in the correct channel -> Confirm sent
  manually. Same seven-step order was already implemented in Phase 4;
  this phase only collapses the block once verified and renames
  `RANGE`'s display label from "Price range" to "Range" per this
  phase's exact wording.
- Closing: Review technician closing -> Verify final amount -> Verify
  payment method -> Copy company closing -> Send manually in the
  correct channel -> Confirm sent manually -> Complete job. The amount
  and payment method now render as two separate checked lines instead
  of one combined sentence, so a missing one is obvious at a glance.

**"Review" is deliberately not a separate tap.** It is the
always-visible summary at the top of every card/workspace (source,
ticket, service, technician, destination). The existing
missing-information/conflicting-field gate already blocks every action
button until that data is resolved -- that gate *is* the enforced
review step. Adding a no-op "I reviewed this" checkbox on top of an
already-enforced gate would be a dead tap with no state behind it, so
one was not added.

### Mobile information hierarchy

Enforced top-to-bottom on every workspace: next action -> job identity
-> technician/channel -> critical facts -> message to copy -> previous
steps (collapsed) -> Technical details (collapsed). This matches the
existing Phase 4 card structure; the only structural change is Active
Jobs' new `Next:` line moving to the very top, above the ticket/company
header line that used to lead.

### Queue priority and "Needs action / Waiting / All"

- **New Jobs** already sorted urgent-first, then oldest-received
  (`luxor_desk_read_model.get_live_dispatch_queue`, unchanged this
  phase). Added a `Needs action / Waiting / All` view: *Waiting* means
  the dispatcher already tapped something for this job (K sent and/or
  WhatsApp opened) but hasn't confirmed dispatch yet -- "come back and
  finish this one" -- *Needs action* is everything untouched or blocked
  by missing/conflicting data. Every live-queue job needs *some*
  dispatcher action by construction (that's what makes it live), so
  this is a real, data-backed split, not an invented one.
- **Active Jobs**: *Needs action* = no technician update yet, an
  ambiguous ("Custom") update, or the job is ready to close. *Waiting*
  = the latest technician update has been recorded and its reply
  already sent -- dispatcher is waiting on the technician's next
  contact. Sorted oldest-actionable-first within each bucket (no
  urgency concept exists for active jobs today, so age is the only
  honest ordering signal).
- **Closings**: *Needs action* = a closing has already been recorded
  and needs to be sent/completed. *Waiting* = the job is dispatched and
  "In Progress," waiting on the technician to report the actual
  closing -- this maps directly to `get_closing_candidates()`'s two
  existing branches, no new classification invented.
- Each queue row shows how long it's been waiting via the existing
  `humanize_timestamp`/`format_age` helpers (unchanged functions, now
  surfaced consistently). A `Next job` button advances the current
  selection to the next entry in the active view -- a manual tap, never
  an automatic jump; completing an action still only changes what's
  selected the next time the dispatcher acts, consistent with "never
  automatically perform the next business action."

### What is intentionally not being built this phase

- No technician-facing CRM screen, portal, or login of any kind.
- No GPS/location tracking, no WhatsApp scraping or automation.
- No "waiting" bucket on New Jobs beyond the tapped-but-unconfirmed
  definition above -- there is no data-backed notion of a New Jobs
  entry that needs literally nothing from the dispatcher, since items
  that need nothing are not live-queue entries in the first place.
- No commercial-aware branching in the Active Jobs `Next:` sentence
  (e.g. "review the price message" as its own state) -- it is derived
  only from technician-update status and reply-sent state, to avoid
  adding a second, parallel next-action computation that could drift
  from the existing Commercial Update section's own logic.
- No new charts, counters beyond the four workflow-strip stage counts,
  or analytics of any kind.
