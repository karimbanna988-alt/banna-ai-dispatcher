# Customer Onboarding Checklist

Everything to collect from a new customer before their pilot starts,
and how to verify it's correctly configured. Nothing in this document
is customer data — it's the list of blanks to fill in.

## 1. Company and job sources

- [ ] Company/business name as it should appear in the CRM and any
      customer-facing messages.
- [ ] Which job sources they receive work from (e.g. AGN, Main Leads,
      or a source not yet supported — see "New source" below).
- [ ] For each source: a handful of **real, redacted-if-needed** sample
      messages, so parsing can be confirmed against their actual format
      (see `docs/COMMERCIAL_READINESS.md` category 2 — extraction is
      only proven against formats already seen).

## 2. Quo configuration

- [ ] `QUO_API_KEY` for their Quo workspace (Settings → API, requires
      Owner/Admin) — see `docs/INSTALLATION.md` step 4.
- [ ] Confirm `rules/quo_dispatch_groups.yaml` reflects their real Quo
      contact/conversation routing.

## 3. Technician roster

For each technician:

- [ ] Name
- [ ] Service area(s) — must match entries in `rules/service_areas.yaml`
      exactly, or `scripts/validate_configuration.py` will flag it as an
      ERROR.
- [ ] Skills (e.g. "car lockout", "house lockout") — used by the
      eligibility/routing logic.
- [ ] Vehicle restrictions, if any (`rules/vehicle_restrictions.json`).
- [ ] Dispatch group — the WhatsApp destination for new-job dispatches.
      Required for every active technician; without it,
      `validate_configuration.py` reports "WhatsApp dispatch is blocked
      for every job."
- [ ] Closing group — the WhatsApp destination for closing/payment
      messages. Optional but recommended.
- [ ] Recurring days off, if any (`rules/service_areas.yaml`'s
      technician-availability fields).

Use `python scripts/validate_configuration.py --export-template roster.csv`
to generate a blank starting template with each technician's id/name/
areas already filled in and dispatch/closing group columns left for a
human to complete.

## 4. Service areas

- [ ] Confirm every area the customer operates in exists in
      `rules/service_areas.yaml` with the correct city/state.
- [ ] Confirm any market aliasing needed (e.g. "Atlantic City" resolving
      to the right canonical area — see `_validate_routing()` in
      `scripts/validate_configuration.py`).

## 5. SLA targets and closing rules

- [ ] What counts as "stale" for an active job (surfaces in Attention
      Now) — currently a fixed target in code; confirm it matches this
      customer's expectations or flag as a customization request.
- [ ] Closing/payment recording conventions (cash vs. card vs. company
      share/technician share splits) — confirm the existing
      `record_job_closing()` fields (`final_amount`, `payment_method`,
      `parts`, `company_share`, `technician_share`) cover their model.

## 6. KPI definitions and terminology

- [ ] Walk through `docs/kpi_definitions.md` with the customer and get
      explicit sign-off that each metric means what they think it
      means — see `docs/COMMERCIAL_READINESS.md` category 5. Do not
      present a KPI as agreed until this happens.
- [ ] Confirm terminology (e.g. "In Progress" vs. their internal term
      for the same state) doesn't need customer-specific relabeling.

## 7. WhatsApp (if enabling capture)

- [ ] Confirm the customer understands this is **receive-only**, via an
      **unofficial** library — never represent this as an official
      WhatsApp Business API integration (see `docs/KNOWN_LIMITATIONS.md`).
- [ ] Identify which WhatsApp groups should be captured (allowlisted).
- [ ] Someone with access to the paired WhatsApp account must be
      physically present once to scan the pairing QR code
      (`whatsapp_shadow_bridge\scripts\start_pairing.ps1`).

## 8. Dispatch groups and closing groups

- [ ] Confirm every dispatch/closing group string configured per
      technician (step 3) is the exact, correct WhatsApp destination —
      a typo here silently misroutes real dispatch messages.

## Training checklist (for the dispatcher who'll use this daily)

- [ ] Walk through the sales demo script (`docs/SALES_DEMO_SCRIPT.md`)
      live, on their own machine, with their own login.
- [ ] Cover: reviewing a new job, editing extracted fields, approving/
      rejecting, assigning a technician, recording a technician update,
      recording and completing a closing, reading the Command Center.
- [ ] Cover what to do if `tools/system_readiness.py` reports a problem
      (`docs/OPERATIONS_RUNBOOK.md`).
- [ ] Cover backup basics (`docs/BACKUP_RESTORE_RUNBOOK.md`) — at
      minimum, who is responsible for running backups and how often.

## Pilot acceptance checklist

Before calling the pilot "live":

- [ ] `python tools/system_readiness.py` reports no FAIL/ACTION REQUIRED.
- [ ] `python scripts/validate_configuration.py` reports zero ERRORs.
- [ ] A real backup has been taken and verified
      (`python scripts/verify_backup.py <path>`).
- [ ] The customer has read and acknowledged
      `docs/KNOWN_LIMITATIONS.md`.
- [ ] The customer has read and signed off on
      `docs/PRODUCTION_CHECKLIST.md`.
- [ ] At least one real end-to-end job has been processed successfully
      (received → reviewed → dispatched → closed) with the customer
      watching.

## New source (not yet supported)

If the customer's job source isn't AGN or Main Leads, this is a real
scoping conversation, not a checkbox: it needs a new message-format
parser (`src/dispatcher/parser.py`) built and tested against real
samples before onboarding can proceed for that source. Treat as a
paid customization (see `docs/PRICING_AND_PACKAGES.md`).
