# Pricing and Packages

**This is a business proposal for discussion, not implemented
billing.** No subscription, invoicing, or metering exists in this
codebase (deliberately out of scope this sprint — see
`docs/KNOWN_LIMITATIONS.md`). Prices below are suggested ranges to
anchor a conversation, not quotes.

## Package 1 — Pilot

**For:** a single business testing LUXOR for the first time, one
location, one dispatcher team, one job source.

**Setup scope:**
- Install on the customer's machine (`docs/INSTALLATION.md`).
- Onboard one job source, the technician roster, service areas
  (`docs/CUSTOMER_ONBOARDING.md`).
- Optional: WhatsApp receive-only capture for one group set.
- One live training walkthrough with the dispatcher.

**Monthly support scope:** best-effort, direct-to-owner support;
`tools/system_readiness.py` output as the primary diagnostic exchanged
in support conversations.

**Limitations:** single shared login, no multi-location, no SLA
commitment, local-machine deployment (customer's own hardware/network
uptime).

**Suggested price range:** low, pilot-friendly — the goal is proving
value, not maximizing revenue, per `docs/COMMERCIAL_READINESS.md`
category 21's need for a real measured outcome.

**Onboarding requirements:** full `docs/CUSTOMER_ONBOARDING.md`
checklist, `docs/PRODUCTION_CHECKLIST.md` sign-off before go-live.

**Optional paid customization:** a new job-source parser if their lead
source isn't AGN/Main Leads (real scoping work, priced separately).

## Package 2 — Professional

**For:** an established business past the pilot stage, wanting
multiple job sources and/or a more involved support relationship.

**Setup scope:** everything in Pilot, plus multiple job sources, fuller
WhatsApp group coverage, and a documented rollback/backup schedule
tailored to the customer's operating hours.

**Monthly support scope:** a defined response-time expectation (to be
negotiated — no SLA infrastructure exists yet to enforce one
automatically), periodic health-check review using
`tools/system_readiness.py`.

**Limitations:** still single shared login (no per-user roles/
permissions yet), still local-machine deployment, still no automated
alerting — someone has to run the health check.

**Suggested price range:** mid, reflecting more onboarding scope and
support commitment than Pilot.

**Onboarding requirements:** same checklist as Pilot, plus a written
SLA/response-time agreement.

**Optional paid customization:** additional job-source parsers,
custom KPI definitions, closing-rule adjustments.

## Package 3 — Multi-location

**For:** a business operating multiple locations/teams wanting a
shared or per-location LUXOR deployment.

**Important, disclosed limitation:** true multi-tenancy (isolated data
per location within one deployment) does not exist in this codebase
(explicitly out of scope this sprint). "Multi-location" today means
**separate deployments** per location, each with its own database,
technician roster, and login — not one shared system with location
isolation. Do not sell this package as single-instance multi-tenancy.

**Setup scope:** one deployment + onboarding per location, following
the Professional package's scope for each.

**Monthly support scope:** consolidated support relationship across
locations, still human-driven (no centralized dashboard across
deployments exists yet).

**Suggested price range:** scales roughly per-location off the
Professional package, with a modest discount for the consolidated
relationship — not a flat enterprise rate, since the underlying system
isn't built for centralized multi-location management yet.

**Onboarding requirements:** full checklist per location.

**Optional paid customization:** a real multi-tenant architecture, if
and when a customer's volume justifies that investment — a genuine new
project, not a configuration change.

---

## What none of these packages include (be explicit with prospects)

- Guaranteed ROI or efficiency numbers — no pilot has produced these
  yet (`docs/COMMERCIAL_READINESS.md` category 21).
- Enterprise-readiness claims — see `docs/KNOWN_LIMITATIONS.md`.
- An uptime SLA — local deployment, no managed hosting.
- Official WhatsApp Business API — receive-only, unofficial library.
- Multi-user accounts/roles, or subscription billing/metering — none
  exist in the codebase today.
