# LUXOR — Product Overview

## What it does

LUXOR is a dispatch CRM for locksmith (and similar field-service)
businesses that receive jobs through multiple channels — SMS-based
lead platforms (via Quo) and WhatsApp group chats — and need those jobs
turned into a consistent, trackable workflow: intake → review →
dispatch → technician tracking → closing → reporting.

## Who it's for

A locksmith or similar field-service business that:

- Receives jobs from one or more lead sources in inconsistent,
  free-text formats.
- Dispatches to a roster of technicians, often coordinated over
  WhatsApp.
- Currently tracks all of this manually or in a general-purpose tool
  not built for this workflow.
- Wants visibility into job status, technician activity, and closing
  amounts without building or buying a large enterprise dispatch
  platform.

## Problems solved

- **Inconsistent intake.** Job messages from different sources arrive
  in different formats. LUXOR extracts structured data (customer,
  address, ticket number, service type) automatically, but never
  commits it without a human reviewing it first.
- **Lost visibility.** Once a job is texted or WhatsApp'd to a
  technician, it's easy to lose track of its status. LUXON keeps a
  single, real-time Command Center view of every job's state.
- **No audit trail.** Every action (approve, edit, reject, assign,
  status update, closing) is recorded with a timestamp, forming a real
  history — not just the day's message it happened.
- **Manual KPI tracking.** Job counts, cancellation rate, technician
  activity, and closing amounts are computed automatically from real
  data, not compiled by hand at the end of the week.

## AI vs. deterministic responsibilities — and the human approval boundary

This is the most important thing to understand about how LUXOR works,
and it should be explained to every customer:

- **AI does exactly one thing:** extract structured fields (customer
  name, address, ticket number, service type) from a raw incoming
  message. It never decides what happens next.
- **Everything after that is deterministic code**, not AI: status
  transitions, routing eligibility, idempotency, closing math. These
  rules are fixed, tested, and don't hallucinate.
- **A human always approves, edits, or rejects the AI's extraction**
  before it becomes a real job. No job is ever created, dispatched, or
  closed by AI output alone.

This boundary is enforced in code, not just policy — the review screen
is a mandatory step in the workflow, not an optional one.

## CRM and KPI capabilities

- Command Center: real-time job counts by status, Attention Now
  (stale/urgent jobs), charts by period, verified closings total.
- Jobs / Active / Closings / Completed screens for the full job
  lifecycle.
- Technician roster with service areas, skills, and dispatch/closing
  group configuration.
- Performance and KPI reporting (see `docs/kpi_definitions.md` for
  exact definitions).
- Archive/Inbox Review for messages that never became jobs.

## WhatsApp/Quo limitations (stated plainly)

- **WhatsApp: receive-only**, via an unofficial library
  (`whatsapp-web.js`) — not an official WhatsApp Business API
  integration, and never represented as one. No outbound send
  capability exists anywhere in the codebase.
- **Quo: read-only** polling of the Quo API — LUXOR never sends a
  message or writes to a customer's Quo account.
- See `docs/KNOWN_LIMITATIONS.md` for the full, current list.

## Deployment model

Runs locally on a dedicated Windows machine (or a machine the customer
controls), optionally exposed remotely via a tunnel. Not a hosted SaaS
product today — see `docs/COMMERCIAL_READINESS.md` category 18.

## Measurable pilot outcomes (what a pilot should measure — not yet measured)

LUXOR does not yet have a completed paid pilot to cite numbers from
(see `docs/COMMERCIAL_READINESS.md` category 21). What a pilot should
measure, using the KPIs LUXOR already computes:

- Time from job received to dispatched.
- Cancellation rate before vs. after adoption.
- Technician response/close-out consistency.
- Dispatcher time spent per job (qualitative, from the dispatcher
  themselves, until instrumented).

No ROI claim should be made to a prospect until a real pilot has
produced these numbers.
