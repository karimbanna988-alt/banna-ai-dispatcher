# Commercial Readiness Scorecard

Written at the end of a focused sprint whose explicit goal was to make
the product installable, demonstrable, supportable, and honestly
assessable for a first paid pilot — not to build new dispatch features.
Every claim below is backed by a named file, test, or script in this
repository; nothing here is aspirational. Sellability status uses four
levels:

- **READY** — built, tested, and usable today with no caveats beyond
  normal operational care.
- **PILOT READY** — built and tested, but only proven under synthetic/
  developer usage; needs a real pilot customer's real usage to be
  proven at commercial scale.
- **BLOCKED** — a real, disclosed gap stands between here and selling
  this to a customer for this category.
- **FUTURE** — deliberately out of scope for a first pilot (e.g.
  billing/multi-tenancy), not a defect.

Categories are also tagged with what's needed to close the gap:
**(A)** finishable in code with no further input, **(B)** needs real
pilot usage to prove out, **(C)** needs a customer decision, **(D)**
needs an official external integration this product does not have.

---

## 1. Core dispatch workflow

**Current state:** New job intake → AI extraction → deterministic
classification → dispatcher approve/edit/reject → technician
assignment → status updates → closing → audit trail, all wired through
`DispatcherService` (`src/dispatcher/application.py`) and exposed via
`operator_api.py` + `operator_ui_v2_connected/`. The technician
status-update → company-reply leg (record technician status, prepare
the deterministic company reply, mark it sent manually) was completed
this sprint — it existed at the service layer, tested, but had no HTTP
route or UI before; see `docs/END_TO_END_WORKFLOW_AUDIT.md` steps 7-8.

**Evidence:** `tests/test_operator_api.py` (including the new
`test_technician_update_*`/`test_company_update_*` tests),
`tests/test_application_*`, the seeded demo walkthrough
(`docs/DEMO_RUNBOOK.md`) covering New → Assigned → In Progress →
Closing needed → Closed → Cancelled.

**Sellability status: PILOT READY (B).** The mechanics are real and
tested; only real dispatcher usage under real call volume will surface
workflow friction synthetic tests can't.

## 2. AI interpretation

**Current state:** `src/dispatcher/parser.py` extracts structured job
data from source-formatted messages (AGN, Main Leads headers); always
routed through a human approve/edit/reject step before becoming a real
job — no AI output is ever auto-committed.

**Evidence:** `tests/test_parser.py`, the mandatory review screen in
`operator_ui_v2_connected/`.

**Sellability status: PILOT READY (B, C).** Extraction accuracy is only
validated against the message formats already seen from AGN and Main
Leads. A new customer's job-source format needs (C) a sample of their
real message formats to confirm/extend parsing, and (B) real volume to
measure true accuracy.

## 3. Deterministic safety

**Current state:** Status transitions, idempotency keys, and dispatch
rules are pure deterministic code (`storage.py`, `action_names.py`) —
never inferred by AI. Documented in `docs/OPERATIONAL_READINESS.md`
("What is implemented").

**Evidence:** `tests/test_idempotency*`, fault-injection tests, the
concurrency tests referenced in `docs/SQLITE_CONNECTION_POLICY.md`.

**Sellability status: READY.** This is the one category this sprint
found nothing new to change — it was already solid, per this sprint's
own instruction not to touch deterministic rules without a proven
defect.

## 4. CRM usability

**Current state:** `operator_ui_v2_connected/` — Command Center, Jobs,
Active, Closings, Completed/History, Performance, Technicians, Archive/
Inbox Review, Learning Review. Mobile and desktop responsive (verified
in prior rounds at 4 viewport sizes).

**Evidence:** `docs/command_center.md`, prior rounds' browser
verification, `docs/DEMO_RUNBOOK.md`'s screen list.

**Sellability status: PILOT READY (B).** No known UI blockers; needs a
real dispatcher's daily workflow to validate ergonomics under pressure.

## 5. KPI/reporting

**Current state:** `src/dispatcher/kpi_read_model.py` — deterministic,
read-only, real-field-only KPIs (job counts by status, cancellation
rate, closing amounts, technician activity). Command Center's charts
and Performance screen consume this directly.

**Evidence:** `tests/test_kpi_read_model.py`, `docs/kpi_definitions.md`.

**Sellability status: PILOT READY (B, C).** KPI *definitions* need (C)
customer sign-off that they match how the customer already measures
their business (see `docs/CUSTOMER_ONBOARDING.md`'s terminology
checklist) before being presented as "the numbers."

## 6. WhatsApp integration

**Current state:** Receive-only, via an **unofficial** `whatsapp-web.js`
bridge (`whatsapp_shadow_bridge/`), now with durable SQLite-backed
storage (`whatsapp_pending_observations` table, added this sprint's
immediately-preceding round) — the real live-pilot message-loss defect
found and fixed just before this sprint is confirmed resolved by
`tools/system_readiness.py`'s durability/duplicate-ID checks.

**Evidence:** `tests/test_whatsapp_bridge_adapter.py`,
`whatsapp_shadow_bridge/test/*`, the real restart-persistence drill
performed the round before this one.

**Sellability status: PILOT READY (D).** No outbound send capability
exists anywhere in this codebase (matches the sprint's own instruction
never to claim this as official). Sellable as "receive and see WhatsApp
updates in the CRM," never as "official WhatsApp Business integration."
(D) because a real, sanctioned WhatsApp Business Platform integration
would need Meta's official Cloud API — see `docs/whatsapp_groups_readiness.md`
and `docs/KNOWN_LIMITATIONS.md`.

## 7. Quo integration

**Current state:** Read (`GET`)-only polling via `src/dispatcher/quo_client.py`
+ `quo_ingest.py`. No send/write capability exists in the client module
by construction (a structural test greps for `.post(`/`.put(`/`.delete(`).

**Evidence:** `tests/test_quo_client.py`'s GET-only guard,
`scripts/quo_probe_readonly.py`.

**Sellability status: PILOT READY (C).** Works against the real Quo API
today. (C) because each new customer needs their own `QUO_API_KEY` and
confirmation their Quo workspace's message format matches what the
parser expects (same caveat as category 2).

## 8. Data durability

**Current state:** All state lives in SQLite (`data/dispatcher.db`)
with WAL journaling, FK constraints, and (as of this sprint's
predecessor round) durable WhatsApp observation storage — nothing
operationally important is held only in process memory anymore.

**Evidence:** `docs/SQLITE_CONNECTION_POLICY.md`,
`tools/system_readiness.py`'s durability check (confirmed PASS against
the real database as of this sprint).

**Sellability status: READY.**

## 9. Backup and recovery

**Current state:** `scripts/backup_database.py` (SQLite-safe online
backup, now with automatic retention — added this sprint),
`scripts/verify_backup.py`, `scripts/restore_database.py` (restore to a
temporary target only, structurally refuses the live DB path).

**Evidence:** `tests/test_backup_restore.py` (28 tests, all passing),
`docs/BACKUP_RESTORE_RUNBOOK.md`, a real restore drill executed in a
prior round.

**Sellability status: READY.** Only manual (a human runs the backup
command / a scheduled task would need to be configured per customer,
see `docs/PRODUCTION_CHECKLIST.md`) — not a gap, a deployment choice.

## 10. Installation

**Current state:** `docs/INSTALLATION.md` (written this sprint) covers
supported versions, dependency install, `.env` setup, DB creation,
startup/shutdown/health-check commands. `scripts/start_crm.ps1` /
`stop_crm.ps1` / `crm_health_check.ps1` are the sanctioned entry points.

**Evidence:** the scripts above; `tools/windows_launcher/` was newly
found this sprint (via `tools/system_readiness.py`'s check) to still
launch the **retired** `luxor_desk.py` Streamlit app instead of the
current `operator_api.py` CRM — a real, disclosed gap.

**Sellability status: BLOCKED (A).** Purely a code fix (repoint or
retire `tools/windows_launcher/`), not attempted this sprint (out of
the sprint's declared scope — it's an unrelated feature-adjacent fix,
not commercial-readiness documentation/tooling). Until fixed, the
one-click Windows launcher is actively misleading and
`scripts/start_crm.ps1` must be used directly.

## 11. Configuration

**Current state:** `scripts/validate_configuration.py` (technician/
routing/policy validator, read-only) + `.env`/`.env.example` for
secrets. `docs/CUSTOMER_ONBOARDING.md` (written this sprint) enumerates
every value a new customer must supply.

**Evidence:** the validator script; `rules/*.yaml` config files.

**Sellability status: PILOT READY (C).** Mechanically sound; every new
customer needs (C) to actually supply their real technician roster,
service areas, and dispatch-group mappings — there is no way around
that being a per-customer conversation.

## 12. Security and secrets

**Current state:** Secrets live only in gitignored `.env` files, never
logged (`log_message` overridden to no-op on the bridge adapter's HTTP
server), never printed by any tool in this repo — verified this sprint
by `tools/system_readiness.py`'s fingerprint-only secret checks and
`docs/SECURITY_REVIEW.md` (written this sprint).

**Evidence:** `docs/SECURITY_REVIEW.md`; `tests/test_security_redaction.py`.

**Sellability status: PILOT READY (A).** One real finding from this
sprint: `operator_api.py` auto-generates and persists a new session
secret to the real `.env` the moment it's imported without one already
set — including, it turns out, from an ill-considered test run during
this very sprint (see `docs/OPERATIONS_RUNBOOK.md`'s "Known operational
risk" section). A guard rail (e.g. requiring an explicit opt-in env var
before auto-provisioning, or only doing it from `if __name__ ==
"__main__"`) is recommended before broader use, not yet implemented.

## 13. User authentication

**Current state:** Single shared dispatcher password
(`LUXOR_DEMO_PASSWORD`), HMAC-compared, rate-limited, HttpOnly+SameSite
session cookie, CSRF token for mutations.

**Evidence:** `tests/test_operator_api.py`'s login/session tests.

**Sellability status: PILOT READY (C).** No per-user accounts or roles
— one shared password for the whole team. Fine for a small pilot team;
a customer with multiple dispatchers who need individual accountability
would need (C) to accept this limitation or wait for a future
multi-user phase (explicitly **FUTURE** — out of scope this sprint per
its own "no multi-tenancy" instruction).

## 14. Monitoring and diagnostics

**Current state:** **New this sprint** — `tools/system_readiness.py`,
a single read-only, nontechnical-operator-facing health check covering
CRM/adapter/bridge liveness, WhatsApp pairing + allowlist size, SQLite
integrity/FK checks, backup-destination writability, secret presence +
cross-file fingerprint matching, startup-script currency, and WhatsApp
observation durability/duplicate-ID checks.

**Evidence:** `tests/test_system_readiness.py` (27 tests),
`docs/OPERATIONS_RUNBOOK.md`.

**Sellability status: READY.**

## 15. Customer onboarding

**Current state:** **New this sprint** —
`docs/CUSTOMER_ONBOARDING.md` (what to collect: company/source names,
Quo contact IDs, technician roster, service areas/skills, dispatch/
closing groups, SLA targets, KPI terminology sign-off, training and
pilot-acceptance checklists).

**Evidence:** the document itself; `scripts/validate_configuration.py`'s
`--export-template` flag for the technician roster.

**Sellability status: PILOT READY (C).** The checklist exists; running
it against a real customer for the first time is what would prove it
complete.

## 16. Documentation

**Current state:** Substantially expanded this sprint:
`docs/INSTALLATION.md`, `docs/CUSTOMER_ONBOARDING.md`,
`docs/PRODUCTION_CHECKLIST.md`, `docs/PRODUCT_OVERVIEW.md`,
`docs/SALES_DEMO_SCRIPT.md`, `docs/PRICING_AND_PACKAGES.md`,
`docs/KNOWN_LIMITATIONS.md`, `docs/SECURITY_REVIEW.md`,
`docs/OPERATIONS_RUNBOOK.md`, `docs/DEMO_RUNBOOK.md`, plus this
document — on top of the substantial pre-existing set
(`docs/BACKUP_RESTORE_RUNBOOK.md`, `docs/kpi_definitions.md`,
`docs/command_center.md`, `docs/whatsapp_groups_readiness.md`, etc.)

**Sellability status: READY.**

## 17. Demo readiness

**Current state:** **New this sprint** — `scripts/start_demo.ps1` /
`stop_demo.ps1`, an isolated demo database, a visible red DEMO MODE
banner, zero Quo/WhatsApp connectivity by construction.

**Evidence:** `tests/test_demo_isolation.py` (12 tests),
`docs/DEMO_RUNBOOK.md`, a real manual run-and-verify-and-stop performed
this sprint (banner confirmed rendering, real database confirmed
unchanged afterward).

**Sellability status: READY.**

## 18. Production deployment

**Current state:** Runs locally on Windows via `python operator_api.py`
+ optional `cloudflared` tunnel for remote access (see
`docs/start_dispatcher_windows.md` from a prior round). No containerization,
no managed hosting, no process supervisor/auto-restart.

**Evidence:** `.claude/launch.json`; prior rounds' tunnel deployments.

**Sellability status: BLOCKED (C, D).** This is a local-machine
deployment model, disclosed as such — not a cloud SaaS. Selling this
commercially requires the customer to (C) accept that model (a
dedicated always-on Windows machine, no vendor-hosted uptime SLA) or
(D) wait for a hosted deployment path, which does not exist yet and is
a real product decision, not a quick fix.

## 19. Legal/platform dependency risks

**Current state:** Documented plainly in `docs/KNOWN_LIMITATIONS.md`
(written this sprint): reliance on an unofficial `whatsapp-web.js`
library that could break or be blocked by a WhatsApp update at any
time, with no SLA or support contract from Meta.

**Evidence:** `docs/KNOWN_LIMITATIONS.md`.

**Sellability status: BLOCKED (D).** This is a genuine, disclosed,
external, ongoing risk inherent to any unofficial WhatsApp automation
approach — not something this codebase can fix. Selling this requires
the customer to explicitly accept that risk in writing (see
`docs/PRODUCTION_CHECKLIST.md`).

## 20. Support burden

**Current state:** One-person-operable via `tools/system_readiness.py`
+ the runbooks written this sprint, but no ticketing system, no SLA
tracking, no automated alerting (a human must run the health check;
nothing pages anyone).

**Evidence:** `docs/OPERATIONS_RUNBOOK.md`.

**Sellability status: PILOT READY (B, C).** Fine for a single pilot
customer with a direct line to the owner. Scaling to multiple paying
customers needs (C) a support-process decision (response-time
commitments, escalation) this sprint deliberately did not make (no
subscriptions/billing/multi-tenancy per its own instructions).

## 21. Commercial proof/ROI evidence

**Current state:** None yet. Every KPI and workflow claim in this
sprint's other documents is backed by tests against synthetic data, not
a real customer's measured before/after outcome.

**Evidence:** N/A by definition — this is exactly what's missing.

**Sellability status: BLOCKED (B).** The single biggest remaining gap
before recurring paid sales: there is no case study, no measured pilot
outcome, no customer testimonial. `docs/SALES_DEMO_SCRIPT.md` is
explicit about not claiming ROI numbers that don't exist yet. This can
only be closed by (B) running an actual paid pilot and measuring it.

---

## Summary table

| # | Category | Status |
|---|---|---|
| 1 | Core dispatch workflow | PILOT READY |
| 2 | AI interpretation | PILOT READY |
| 3 | Deterministic safety | READY |
| 4 | CRM usability | PILOT READY |
| 5 | KPI/reporting | PILOT READY |
| 6 | WhatsApp integration | PILOT READY |
| 7 | Quo integration | PILOT READY |
| 8 | Data durability | READY |
| 9 | Backup and recovery | READY |
| 10 | Installation | BLOCKED |
| 11 | Configuration | PILOT READY |
| 12 | Security and secrets | PILOT READY |
| 13 | User authentication | PILOT READY |
| 14 | Monitoring and diagnostics | READY |
| 15 | Customer onboarding | PILOT READY |
| 16 | Documentation | READY |
| 17 | Demo readiness | READY |
| 18 | Production deployment | BLOCKED |
| 19 | Legal/platform dependency risks | BLOCKED |
| 20 | Support burden | PILOT READY |
| 21 | Commercial proof/ROI evidence | BLOCKED |

**Bottom line:** nothing here claims enterprise-readiness or
autonomous operation. This is an honest PILOT READY product with four
disclosed BLOCKED items (one a real but small code fix — the launcher;
three that are inherent deployment/legal/proof-of-value realities no
amount of further coding resolves) — appropriate for a first paid pilot
with an informed customer, not yet appropriate for unsupervised
multi-customer sales.

## Addendum — Operator Speed Implementation Sprint

Category 4 (CRM usability) gained real, tested, live-verified
improvements this sprint: a Fast Path that auto-navigates the operator
to the right screen with the right job pre-selected at two of the
workflow's screen-transition points (dispatch confirm -> Active,
technician-completed -> Closings), a 3-group Action Center presentation
with a deterministic priority label, consistent Copy/Mark-sent button
wording across all four prepared-outbound-message flows, and safe
desktop keyboard shortcuts. Full before/after quantification (11.5%
modeled effort reduction for a full Scenario A job, measured
route_active/route_action_center latency improvements of up to ~48% at
volume) is in `docs/WORKFLOW_SPEED_AUDIT.md`. This does not change
category 4's PILOT READY status — the underlying mechanics were already
real and tested; this sprint made using them faster, not fundamentally
different in kind. A real, evidenced reload-loses-CSRF-token bug
(category 13, user authentication) was also found and fixed in the same
sprint — see `docs/SECURITY_AUDIT.md` finding #5's update.
