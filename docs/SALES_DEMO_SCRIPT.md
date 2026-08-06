# Sales Demo Script

A 10–15 minute walkthrough using the sanitized demo
(`scripts/start_demo.ps1` — never the real system or real customer
data). Follow `docs/DEMO_RUNBOOK.md` to start it before the meeting.

## Before you start

- Start the demo ahead of time (`scripts\start_demo.ps1`), confirm the
  red DEMO MODE banner is visible, and log in once so the meeting
  starts on the Command Center, not the login screen.
- Have `docs/KNOWN_LIMITATIONS.md` open in another tab — you will refer
  to it, not read from memory.

## 1. Login

Show the login screen briefly — one shared password per team today
(mention this is a pilot-scale limitation, not hidden — see "What NOT
to claim" below).

## 2. Command Center

Point out: job counts by status, the charts, Attention Now (explain
this surfaces jobs that need attention — e.g. no update in over the
SLA target — automatically, not from someone remembering to check).
Point out the Verified Closings total.

## 3. A new incoming job

Open Jobs → New. Show a raw incoming message and its extracted fields
side by side. **Say explicitly**: "The AI only extracts these fields.
Nothing happens automatically — a dispatcher always reviews this
before it becomes a real job."

## 4. AI extraction

Walk through editing one field (e.g. correcting an address) to show
the human is genuinely in control, not just clicking "confirm" on
whatever the AI produced.

## 5. Deterministic rules

Explain that once approved, everything downstream (status transitions,
routing eligibility) is fixed, tested code — not AI, and not guesswork.
This is why the same input always produces the same result.

## 6. Technician assignment

Show assigning the approved job to a technician from Active Jobs,
and explain eligibility is based on the technician's configured service
area and skills (`docs/CUSTOMER_ONBOARDING.md`'s roster fields).

## 7. Incoming technician update

Show a technician status update (e.g. F200's "ETA 15 minutes") flowing
through to the Active board without the dispatcher re-typing anything.

## 8. Closing

Walk through recording a closing (amount, payment method) on job F400
("Completed update — closing needed") through to Closed, and point out
it appears in Verified Closings on the Command Center.

## 9. Audit history

Open Completed/History for job 3 (the pre-closed demo fixture) and
show its full timeline — every action, timestamped.

## 10. KPI impact

Return to Command Center / Performance and show how the numbers you
just changed (by closing a job) update — this is real computed data,
not a static mockup.

---

## What NOT to claim

- **Do not** call the WhatsApp integration "official" or imply Meta/
  WhatsApp Business API involvement. It is receive-only, via an
  unofficial library, and can break if WhatsApp changes something —
  say this plainly if asked.
- **Do not** claim any ROI numbers, time savings, or efficiency
  percentages. None have been measured from a real pilot yet
  (`docs/COMMERCIAL_READINESS.md` category 21). If asked, say a pilot
  is exactly how those numbers get produced.
- **Do not** claim multi-user accounts, roles/permissions, or
  subscription billing exist — they don't yet (see
  `docs/KNOWN_LIMITATIONS.md`).
- **Do not** claim cloud/hosted deployment, uptime SLAs, or enterprise
  scale — this runs locally today (category 18).
- **Do not** demo against real customer data, ever. The demo database
  is entirely fictional by construction.
- **Do not** promise a timeline for a specific new job-source parser or
  feature without checking scope first — see
  `docs/PRICING_AND_PACKAGES.md`'s customization note.

## After the demo

Stop the demo server (`scripts\stop_demo.ps1`) — don't leave it running.
