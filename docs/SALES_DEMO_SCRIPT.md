# Sales Demo Script

A 12-minute walkthrough using the sanitized demo (`scripts/start_demo.ps1`
or `scripts/seed_demo_scenarios.py` for a pre-populated end-to-end
example — never the real system or real customer data). Follow
`docs/DEMO_RUNBOOK.md` to start it before the meeting.

Each step below lists: what you click, what appears, what to say, and
the specific business value that step proves. Total run time assumes
you already have the demo running and seeded — see "Before you start."

## Before you start

- Start the demo ahead of time, seed it with `scripts/seed_command_center_demo_db.py`
  plus `scripts/seed_demo_scenarios.py` (the latter gives you one job
  that's already gone all the way through to Complete, plus one that's
  correctly stuck in Human Review — useful to point at directly instead
  of narrating a hypothetical), confirm the red DEMO MODE banner is
  visible, and log in once so the meeting starts on Command Center.
- Have `docs/KNOWN_LIMITATIONS.md` open in another tab — refer to it,
  don't recite it from memory.

## 1. Login (30s)

**Click:** nothing yet — just show the screen.
**Appears:** the login form, red "DEMO MODE — synthetic data only" banner.
**Say:** "One shared password per team today — a pilot-scale
limitation, not something we're hiding. Everything on screen from here
is fictional."
**Proves:** the demo is honestly labeled; nothing shown is a real
customer's data.

## 2. Command Center (1 min)

**Click:** already the landing screen after login.
**Appears:** KPI tiles, Attention Now, the Active jobs preview, three charts.
**Say:** "This is the daily view — job counts by stage, and Attention
Now surfaces anything that's been sitting too long without an update,
automatically, not because someone remembered to check."
**Proves:** the dispatcher gets a real-time operational picture without
building their own tracking spreadsheet.

## 3. Action Center (1 min)

**Click:** Action Center in the sidebar (or "View all" from Attention Now).
**Appears:** ten sections — new jobs to review, company K owed,
technician assignment required, ready to dispatch, waiting on
technician K, updates to review, company updates owed, closings to
review, closings ready to send, human review required.
**Say:** "This is the single list of everything that needs a human
decision right now, grouped by exactly what's blocking it — sourced
from one backend rule, not ten separate screens each guessing
independently."
**Proves:** nothing falls through the cracks; the dispatcher has one
place to work from, not five tabs to remember to check.

## 4. A new AGN job arrives (1 min)

**Click:** open the "new jobs to review" item from Action Center (or Jobs → New).
**Appears:** the raw incoming message text and the extracted fields
(ticket, address, service, vehicle) side by side.
**Say:** "The AI only extracts these fields. Nothing happens
automatically — a dispatcher reviews this before it becomes a real job."
**Proves:** human-approved extraction, not an autonomous black box.

## 5. AI extraction (30s)

**Click:** edit one field (e.g. correct an address).
**Appears:** the corrected value, still awaiting approval.
**Say:** "The human is genuinely in control here — not just clicking
confirm on whatever the AI produced."
**Proves:** the AI is a drafting assistant, not a decision-maker.

## 6. Deterministic validation (30s)

**Say (no click needed):** "Once approved, everything downstream —
status transitions, routing eligibility, which company needs a K reply
— is fixed, tested code, not AI and not guesswork. The same input
always produces the same result."
**Proves:** predictable, auditable behavior — not a model that might
answer differently tomorrow.

## 7. Company K (1 min)

**Click:** approve the job — the Action Center "company K owed" state.
**Appears:** the prepared "K" acknowledgment text, a "mark sent" action
(never an automatic send).
**Say:** "AGN and Lucky require a K acknowledgment before dispatch. We
prepare the exact text; a human copies it and confirms it went out."
**Proves:** the company-facing acknowledgment step nothing is skipped,
and nothing is auto-sent to a real company account.

## 8. Technician recommendation (1 min)

**Click:** the job now under "technician assignment required."
**Appears:** eligible technicians for this job's area/service, with
reasons; ineligible technicians shown with their rejection reason.
**Say:** "Eligibility is based on the technician's configured service
area and skills — never guessed, and you can see exactly why someone
wasn't recommended."
**Proves:** deterministic routing, not a black-box suggestion.

## 9. Technician dispatch (1 min)

**Click:** confirm dispatch to the selected technician.
**Appears:** the formatted Luxor-branded technician message (never the
original company's raw text), a "mark sent" action.
**Say:** "The technician never sees the company's original formatting —
just our own clean template. Sending is still a manual copy-and-confirm,
same as the K step."
**Proves:** consistent technician-facing communication regardless of
which company the job came from.

## 10. Technician K (1 min)

**Click:** the job's lifecycle stepper, step 6 ("Technician accepted");
show the "waiting for technician K" panel with a captured WhatsApp reply.
**Appears:** the match confidence (exact/strong/ambiguous) and evidence
(group match, ticket match) for the captured reply, with explicit
Confirm-accepted / Confirm-declined actions.
**Say:** "A bare 'K' is never auto-attached — you see exactly why the
system thinks this reply belongs to this job before confirming it."
**Proves:** no false acceptance; a technician's actual reply is
evidence-matched, not assumed.

## 11. Technician update (1 min)

**Click:** record a technician status update (e.g. "Going").
**Appears:** the job moves to "company updates owed" in Action Center;
the deterministic company-facing reply ("Going") is prepared.
**Say:** "The dispatcher never re-types anything — the reply text is
computed from the technician's confirmed status."
**Proves:** the technician-update → company-reply loop closes without
manual drafting.

## 12. Company update (30s)

**Click:** "mark sent" on the prepared update.
**Appears:** the job leaves "company updates owed."
**Proves:** the company is kept informed without extra dispatcher effort.

## 13. Closing (1 min)

**Click:** record the technician's closing (amount, payment method) on
a job that's ready; walk through prepare → mark sent.
**Appears:** the company-specific closing template, rendered from the
technician's real numbers — never recalculated or invented.
**Say:** "The amount is repeated exactly as the technician reported it
— this system never calculates or estimates a price."
**Proves:** financial accuracy and an unbroken chain from technician to
company record.

## 14. Completion, KPI and audit proof (1 min)

**Click:** Complete Job; then Command Center / Performance; then the
job's full audit timeline.
**Appears:** the job disappears from every Action Center section; the
KPI tiles and Verified Closings total update; the timeline shows every
action, timestamped, from arrival to completion.
**Say:** "Every number on this dashboard is computed from real actions
you just watched happen — not a static mockup — and every one of those
actions is in a permanent, timestamped audit trail."
**Proves:** the full lifecycle closes cleanly, and nothing is
unaccounted for.

---

## What NOT to claim

- **Do not** call any outbound step "automatic." Company K, technician
  dispatch, company updates, and closings are all prepare-then-manually-confirm
  — there is no code path in this system capable of sending a real Quo
  or WhatsApp message.
- **Do not** call the WhatsApp integration "official" or imply Meta/
  WhatsApp Business API involvement. It is receive-only, via an
  unofficial library, and can break if WhatsApp changes something —
  say this plainly if asked.
- **Do not** claim Quo write access exists. The Quo client is
  structurally GET-only; there is no method in the codebase capable of
  sending, and connecting one is separate, not-yet-started work.
- **Do not** claim any ROI numbers, time savings, or efficiency
  percentages. None have been measured from a real pilot yet
  (`docs/COMMERCIAL_READINESS.md`). If asked, say a pilot is exactly how
  those numbers get produced.
- **Do not** claim multi-user accounts, roles/permissions, or
  subscription billing exist — they don't yet (see
  `docs/KNOWN_LIMITATIONS.md`).
- **Do not** claim cloud/hosted deployment, uptime SLAs, or enterprise
  scale — this runs locally today.
- **Do not** demo against real customer data, ever. The demo database
  is entirely fictional by construction.
- **Do not** promise a timeline for a specific new job-source parser or
  feature without checking scope first — see
  `docs/PRICING_AND_PACKAGES.md`'s customization note.

## After the demo

Stop the demo server (`scripts\stop_demo.ps1`) — don't leave it running.
