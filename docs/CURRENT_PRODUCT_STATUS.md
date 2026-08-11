# Current Product Status

One concise, current snapshot — read this instead of piecing status
together from sprint checkpoints. Every claim below is backed by a
named file, test, or tool in this repository. Superseded/stale claims
found in other docs during this pass were corrected there directly
(see `docs/KNOWN_LIMITATIONS.md`'s technician-acceptance section for
the most significant example: an entry describing a pre-tracking state
that no longer reflects the product).

## READY NOW

Capabilities genuinely working today, tested, and usable with no
further code work:

- **Full dispatch workflow**: incoming job → review → company K →
  technician assignment → technician dispatch → technician K → technician
  update → company update → closing → company closing → complete/audit.
  Every step requires an explicit human action; nothing auto-sends.
- **Deterministic AI-assisted extraction**: `src/dispatcher/parser.py`
  extracts structured fields from source-formatted messages (AGN, Main
  Leads, Lucky); always routed through mandatory human approve/edit/
  reject before becoming a real job.
- **Technician K (acceptance) tracking**: a real `technician_acceptance`
  table, EXACT/STRONG/AMBIGUOUS/UNMATCHED classification
  (`dispatcher.technician_ack`), full confirm-accepted/declined UI —
  every match still requires an explicit human click, even an EXACT one.
- **Six proven end-to-end scenarios** (`scripts/seed_demo_scenarios.py`):
  happy path, human review (no eligible technician), duplicate/
  correction, technician restriction, incomplete closing, technician
  decline — each with its own regression tests.
- **Action Center**: one unified "what needs a human right now" surface,
  the canonical 10 workflow-stage sections presented as 3 groups (Action
  now / Waiting on someone else / Needs review), deterministic priority
  labels (URGENT/DUE/NORMAL/WAITING — integer buckets, never an AI score).
- **Fast Path navigation**: confirming dispatch and reaching a
  technician-completed job both auto-navigate to the right screen with
  the right job pre-selected; a "Next actionable job" control jumps to
  the highest-priority item without manual scanning.
- **Global search**: ticket/source/technician/city/service, no PII,
  opens directly into a job's correct context regardless of its status.
- **Lifecycle stepper**: an 11-step, backend-computed (never
  independently guessed by the frontend) progress view on every job.
- **Consistent Copy/Mark-sent pattern** across all four
  prepared-outbound flows (company K, technician dispatch, company
  update, company closing); safe desktop keyboard shortcuts
  (`/`, J/K, Enter, Esc, C) — none bound to a mutating action.
- **KPI/reporting**: Command Center and Performance screens, honestly
  showing "Insufficient data" rather than a misleading percentage at
  low volume.
- **Deterministic idempotency** on every mutating action (technician
  update, company update, closing record/prepare/confirm-sent,
  complete) — a double-click or retry never double-records.
- **Security**: authentication required on every non-public route, CSRF
  enforced (and correctly re-established after a page reload), rate-
  limited login, CSP header, generic error responses (no exception
  detail leaked), parameterized SQL throughout, path-traversal-safe
  static file serving, secrets never logged.
- **Fail-closed demo/test isolation**: `dispatcher.db_safety` structurally
  refuses any demo/test-mode path that resolves to the real database;
  enforced by a session-wide test tripwire, not just convention.
- **Backup/restore**: online-backup API (consistent snapshot, not a raw
  copy), independent verification script, retention scoped by exact
  filename, restore structurally refuses to target the live database.
- **Pilot readiness tool** (`tools/system_readiness.py`): DB/FK
  integrity, backup health, technician/service-area mapping
  completeness, Quo GET-only and WhatsApp no-send structural checks,
  bind-address warning, demo-isolation guard, an approximate human-review
  backlog count — every result labeled CODE/CONFIGURATION/EXTERNAL
  INTEGRATION, secrets never printed.
- **Test suite**: 1500+ tests, real database verified unchanged every
  run via a session-wide tripwire fixture.
- **Multi-group WhatsApp shadow dispatcher** (`whatsapp_group_registry`,
  `route_whatsapp_shadow_feed`): watches 85 real WhatsApp groups
  (exact-ID registry, never inferred from a group's name at runtime),
  classifying each observation's role and, for the 15 owner-confirmed
  groups, interpreting it by reusing existing business logic --
  `extract_job`/`classify_incoming` for company job intake, a
  generalized `technician_ack` EXACT/STRONG/AMBIGUOUS/UNMATCHED
  correlation for technician-group messages. A human still clicks
  "Promote to job" (reuses the exact existing
  `confirm_whatsapp_dispatch` commit path, idempotent by WhatsApp
  message_id) or "Attach" (existing flow) before anything becomes real
  job state -- nothing is auto-created, auto-accepted, or auto-closed.
  Outgoing/self messages are captured to a separate audit-only table
  with no path into the actionable queue. See
  `docs/WHATSAPP_ROUTING_MAP_REVIEW.md` for the full group-by-group
  mapping review and open owner decisions.

## OWNER CONFIGURATION REQUIRED

Not code defects — decisions or setup only the owner/operator can make:

- **Technician mappings incomplete.** A real run of
  `tools/system_readiness.py` against the live database found 12 of 15
  active technicians missing a `dispatch_group` and/or `closing_group`.
  Confirm every technician who will receive real dispatches during a
  pilot has both configured first.
- **Server bind address decision.** `operator_api.py` binds `0.0.0.0`
  (all network interfaces), not just loopback. If only the Cloudflare
  tunnel should ever reach this server, change it to `127.0.0.1` — a
  one-line change, but the owner should confirm intent first (see
  `docs/SECURITY_AUDIT.md` finding #9).
- **Duplicate `OPERATOR_API_SESSION_SECRET` line in `.env`.** Known,
  documented, explicitly out of scope for every sprint so far — needs a
  deliberate cleanup decision, not an automated fix.
- **Backup directory ACL hardening.** The backup destination
  (`../ai-dispatcher-db-backups/`) inherits default Windows folder
  permissions; on a shared/multi-user machine, confirm NTFS permissions
  restrict access to the account the CRM runs under.
- **Windows one-click launcher is stale.** `tools/windows_launcher/`
  still starts the retired `luxor_desk.py` Streamlit interface, not the
  current `operator_api.py` CRM. Use `scripts\start_crm.ps1` directly
  until this is repointed (this one is a real code fix, tracked in the
  readiness tool as `(CODE)`, not owner configuration — listed here
  because it changes what command the owner should actually run).

## FUTURE / EXTERNAL

Deliberately out of scope for this build — not a defect, not planned
for a first pilot:

- **Quo outbound writes.** `src/dispatcher/quo_client.py` has no
  POST/PUT/PATCH/DELETE-capable method by design — every outbound
  message is a human copy-paste. A real write integration would be new,
  deliberately-built, explicitly-approved work, not a configuration flip.
- **Official WhatsApp Business API sending.** `whatsapp_shadow_bridge/`
  uses an unofficial library (`whatsapp-web.js`) for receive-only
  capture; no function anywhere in it can send a message (enforced by
  `whatsapp_shadow_bridge/test/no-send-static-check.test.js`). Real
  outbound WhatsApp requires the official Meta Cloud API — a separate
  integration this product does not have.
- **Multi-tenant SaaS.** Single-dispatcher, single-database,
  single-deployment model. No tenant isolation, no per-customer
  provisioning.
- **Billing.** No subscription, metering, or invoicing capability.
- **Enterprise roles/SSO.** One shared password
  (`LUXOR_DEMO_PASSWORD`/production equivalent), no per-user accounts,
  no role-based permissions, no SSO/SAML/OAuth.
- **Proven ROI at scale.** Every workflow-speed and effort-model number
  in `docs/WORKFLOW_SPEED_AUDIT.md` is a comparative engineering
  estimate against synthetic data, explicitly not a measurement of real
  human time or real dispatcher throughput. Only a real pilot's actual
  usage can prove that.
