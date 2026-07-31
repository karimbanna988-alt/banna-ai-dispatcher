# Competitive gap analysis vs. general field-service CRMs (Workiz-class)

This document compares our system against the **general category** of
field-service management software Workiz belongs to, based on publicly
known feature categories that class of product commonly offers. It does
not claim to describe Workiz's actual current feature set, pricing, or
internals, and reproduces no Workiz text, branding, or design.

The goal isn't feature parity — it's being deliberately better at the ~10
things that actually decide whether a locksmith dispatcher gets a job
handled fast and correctly.

| Category | What general field-service CRMs typically offer | What we already provide | What's missing | Matters to us? | Priority | Smallest useful step | Risks / business decisions needed |
|---|---|---|---|---|---|---|---|
| Scheduling & dispatching | Calendar/board view, drag-drop assignment, multi-technician scheduling | Live dispatch queue with live/older split, urgency ordering, one-tap dispatch flow, technician eligibility matching | Calendar/day-view; multi-day scheduling ahead of time | Partially — this business dispatches reactively (emergency jobs), not by pre-booked calendar slots | LATER | A simple "scheduled for later" flag on a job, sorted separately from the live queue | Needs the owner to confirm whether pre-booked jobs are common enough to justify a calendar view at all |
| Customer CRM & service history | Customer profiles, repeat-customer history, notes | Per-job customer fields (name/phone/address), full audit timeline per job | No cross-job customer identity/history (a repeat customer today looks like two unrelated jobs) | Yes, moderately | NEXT | Match by phone number across jobs, show "N prior jobs" on a job card | Needs a decision on whether phone-number matching is reliable enough given multiple job sources reformat numbers differently |
| Technician mobile workflow | Mobile app for techs: accept/decline, status updates, photos, signatures | Technician-facing WhatsApp updates (receive-only bridge), dispatcher-mediated status entry | No direct technician-facing app; no photo/signature capture | Yes, this is a real gap | NEXT | None planned this phase — out of scope (would require a new technician-facing surface) | This is a bigger, separate initiative; not attempted here |
| Invoicing & payment processing | Generate invoices, take card payments in the field | Closing record capture (amount/payment-method as free text), no invoice generation, no payment processing | Real invoicing, card processing | Payment processing: not this business's workflow (cash/manual settlement); invoicing: unclear | LATER | N/A this phase | Needs the owner's decision on whether any invoice generation is wanted at all before scoping |
| Reports & analytics | Dashboards, revenue reports, technician leaderboards | The KPI Performance screen built in this phase (dispatch speed, completion rate, source/technician breakdowns, AI quality, data quality) | Historical trend charts, exportable reports | Yes — this phase's main deliverable | NOW | Delivered this phase (read-only, mobile-first, no charting library) | Financial KPIs (profit/commission) explicitly BLOCKED pending owner-approved formulas |
| Communications | In-app SMS/email/call logging | Quo (SMS-source) GET-only ingestion, WhatsApp receive-only technician-group bridge, deterministic company-reply templates | Two-way in-app messaging, call logging | Partially — see competitive advantages below; this business's real workflow already routes through Quo/WhatsApp | NOT NEEDED as a rebuild — our source-specific ingestion is the actual advantage here | N/A | None |
| Route planning / GPS | Live technician location, route optimization | Manually recorded "city"/"area" per job, technician area eligibility matching | No live GPS, no route optimization | Uncertain — depends on whether technicians would use a location-sharing feature at all | LATER | N/A this phase | Requires a technician-facing app first; also a real privacy/consent decision |
| Inventory | Parts/stock tracking per job or per van | `closing_parts_amount` field exists (free text) but no inventory catalog or stock levels | Full inventory system | Low — locksmith parts are typically small/carried, not warehouse-tracked | NOT NEEDED for now | N/A | Revisit only if the owner reports real inventory-loss problems |
| Lead management | Lead capture forms, lead scoring, follow-up sequences | N/A — jobs arrive already-formed from AGN/Lucky/Main Leads/Quo, not from a lead funnel this business runs itself | Lead-source-agnostic intake form | No — this business doesn't generate its own leads through this system | NOT NEEDED | N/A | None |
| Automation | Auto-assign, auto-remind, workflow triggers | Deterministic parsing/classification, technician eligibility auto-suggestion, all final actions remain human-confirmed by design | Fully automatic dispatch (deliberately not built) | Automation exists where it's safe (parsing); full auto-dispatch is a deliberate non-goal — human-in-the-loop is the design | NOT NEEDED beyond current scope | N/A | This is a stated architectural principle, not a gap |
| Estimates | Send/track pre-job price estimates | Company reply / quote-type capture during the commercial-update flow (STARTING/FIXED/RANGE/etc.) | Formal estimate documents sent to the customer | No — pricing here is negotiated live over the phone/WhatsApp, not via written estimates | NOT NEEDED | N/A | None |
| Integrations | Zapier, QuickBooks, Google Calendar, etc. | Direct Quo API integration (source-specific, not generic) | Generic third-party integration marketplace | No — this business has exactly the integrations it needs (Quo, WhatsApp); a marketplace adds complexity without a real use case | NOT NEEDED | N/A | None |

## Our real competitive advantages (not available in a generic field-service CRM)

1. **AGN / Lucky / Main Leads deterministic parsing** — each source's exact
   message format is parsed by dedicated, tested extractors, not a generic
   "paste text, guess fields" flow.
2. **Direct Quo (SMS-source) ingestion, GET-only** — polls the real source
   platform directly; a generic CRM would need the dispatcher to manually
   copy-paste every incoming message.
3. **Deterministic locksmith-specific routing** — technician eligibility by
   area/skill/vehicle restriction, evaluated the same way every time.
4. **Company-specific reply and closing formats** — the exact reply/closing
   text each source company expects, generated deterministically, not
   freehand every time.
5. **Technician-group WhatsApp routing** — company/K-message dispatch
   flows tailored to how this business's technicians actually work.
6. **Human-in-the-loop by construction** — nothing sends, dispatches, or
   changes status without an explicit human confirmation step; this is a
   safety property, not a missing automation feature.
7. **Message deduplication** — Quo polling and the WhatsApp bridge both
   dedupe by message ID before anything reaches a human, so nothing is
   reviewed twice.
8. **AI Learning Review** — every AI suggestion vs. dispatcher-confirmed
   outcome is captured for later human review (VERIFIED/CORRECTED/EXCLUDE/
   SENSITIVE/INCOMPLETE), building a real, auditable correction record
   instead of an opaque "AI got better" claim.
9. **Mobile-first dispatcher workflow** — 60-minute live window, 5-card cap,
   one-tap primary actions, verified at real phone viewport sizes — not a
   desktop tool with a mobile web view bolted on.
10. **Full auditability** — every status transition traces through one
    deterministic function (`derive_job_status()`) and every event is
    logged; nothing is a black-box state change.
11. **Emergency-job focus** — the entire Jobs/Active design (60-min window,
    urgency ordering, stale-job warnings) is built around "what needs a
    decision right now," which matters far more for lockout emergencies
    than for a generic multi-day service scheduling calendar.
