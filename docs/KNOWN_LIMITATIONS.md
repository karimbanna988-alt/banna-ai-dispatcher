# Known Limitations

A plain, current list — read this before selling or deploying LUXOR to
a new customer. Nothing here is hidden in other documents; this is the
single place to check.

## WhatsApp

- **Unofficial dependency.** WhatsApp capture uses `whatsapp-web.js`
  (`whatsapp_shadow_bridge/`), an unofficial library that automates a
  real WhatsApp Web session. It is not sanctioned by Meta/WhatsApp and
  could break, get rate-limited, or get the linked account blocked if
  WhatsApp changes something on their end — with no advance notice and
  no vendor support to call.
- **No official WhatsApp Business API.** This is not the Meta Cloud
  API. There is no webhook, no message templates, no official SLA.
- **Receive-only, structurally.** No function anywhere in
  `whatsapp_shadow_bridge/` can send a WhatsApp message — enforced by
  `whatsapp_shadow_bridge/test/no-send-static-check.test.js`, not just
  a policy.

## Technician acceptance ("K") tracking — human-confirmed, not auto-accepted

**Corrected this pass** — this section previously described a
pre-tracking state that no longer reflects the product; left uncorrected
it would understate what exists today.

A `technician_acceptance` table (job-keyed) does track accepted/declined
state, and `dispatcher.technician_ack.classify_candidates()` classifies
each captured WhatsApp reply against a job as EXACT / STRONG / AMBIGUOUS
/ UNMATCHED (group-name match, ticket match, acknowledgment/decline
text vocabulary — see that module for the exact rules). What remains a
deliberate limitation, not a gap: **no reply is ever auto-accepted**. A
bare "K"/"ok"/"👍" is surfaced as a candidate the dispatcher must
explicitly confirm (`route_technician_k_confirm_accepted`/
`_confirm_declined`, wired to "Confirm technician accepted"/"declined"
buttons in the Active job detail) — even an EXACT-category match still
requires a human click, by design (see the absolute safety rule against
removing human approval). AMBIGUOUS candidates additionally show their
specific reasons so the dispatcher can judge them, rather than guessing.

## Human review requirement

Every AI-extracted job field must be approved, edited, or rejected by a
human before it becomes a real job. This is not a configurable
convenience — it's how the system is built (see
`docs/PRODUCT_OVERVIEW.md`'s "AI vs. deterministic responsibilities").
There is no "auto-approve" mode.

## Deployment model

- **Local deployment only.** Runs on a machine the customer controls
  (or the vendor controls on their behalf) — not a hosted, multi-tenant
  SaaS product. No managed uptime SLA exists.
- **No process supervision.** If the CRM process crashes, nothing
  automatically restarts it — a human (or a customer-configured
  Windows service/scheduled task, not provided by this codebase) has
  to notice and restart it.

## Operational evidence is pilot-scale only

Every workflow, KPI, and performance claim in this repository's
documentation is backed by tests against synthetic or developer-usage
data — not a completed paid pilot's real, sustained usage. See
`docs/COMMERCIAL_READINESS.md` category 21. Do not represent this
system as "proven at scale" to a prospect.

## External-provider outages

LUXOR depends on two external services it does not control:

- **Quo** (SMS lead platform) — if Quo's API is unreachable or changes
  its response format, job intake from that source stops until fixed.
- **WhatsApp** — see above; an account block or library breakage stops
  WhatsApp capture entirely until re-paired or fixed.

Neither outage silently corrupts data — both fail closed (no messages
are lost or fabricated), but both do interrupt intake until resolved.

## Customer-specific configuration required

Nothing works out of the box for a new customer. Job-source message
formats, technician rosters, service areas, dispatch/closing groups,
and KPI terminology all need to be configured and validated per
customer — see `docs/CUSTOMER_ONBOARDING.md`. A new, previously-unseen
job-source format requires new parser code, not just configuration.

## Role/permission limitations

- **One shared password** for the entire team
  (`LUXOR_DEMO_PASSWORD`) — no individual dispatcher accounts, no
  role-based permissions, no per-user audit attribution beyond "a
  dispatcher did this."
- Every authenticated user has the same, full access to every screen
  and action. There is no read-only or restricted role.

## No built-in subscription billing

No metering, invoicing, payment processing, or subscription management
exists in this codebase. `docs/PRICING_AND_PACKAGES.md` describes
business packages for manual sales/billing — none of it is automated.

## Memory-based / local-only components still present

Most operationally important state is now durable (SQLite), including
WhatsApp observations as of the round immediately before this sprint.
What remains memory-based or local-file-based by design, disclosed
here:

- **Session tokens** (`operator_api.py`'s `_ACTIVE_SESSIONS`) — an
  in-memory set per running process. A CRM restart logs out every
  active session (by design — "a server restart itself acts as 'log
  everyone out'"). Session tokens are HMAC-signed with a shared secret
  (`OPERATOR_API_SESSION_SECRET`), so a valid signature from one
  `operator_api.py` process is a valid signature for any other process
  sharing that same secret (e.g. the demo instance, which inherits it
  from the same `.env`) — see `docs/DEMO_RUNBOOK.md`'s "Known
  interaction" section for an observed-but-not-yet-fully-root-caused
  case of this during this sprint's own testing.
- **Login rate-limiting** (`_LOGIN_ATTEMPTS`) — in-memory per process;
  resets on restart.
- **WhatsApp allowlist and message-dedupe state**
  (`whatsapp_shadow_bridge/`'s `allowlist.json`, `seen-message-ids.json`)
  — local JSON files, not a database. Durable across restarts, but not
  backed up by `scripts/backup_database.py` (that only backs up the
  SQLite database) — a full disaster-recovery plan for a WhatsApp-
  enabled deployment should include these files, not just the database.
- **The retry/delivery queue** (`whatsapp_shadow_bridge`'s
  `pending-delivery.json`) — same category: durable, local-file-based,
  not covered by the SQLite backup tooling.

## The Windows one-click launcher is stale

`tools/windows_launcher/` still starts the retired `luxor_desk.py`
Streamlit interface, not the current `operator_api.py` CRM. Use
`scripts/start_crm.ps1` directly. See
`docs/COMMERCIAL_READINESS.md` category 10.

## Operational risk: `.env` auto-provisioning on import

`operator_api.py` generates and appends a new
`OPERATOR_API_SESSION_SECRET` to the real `.env` the moment the module
is imported and doesn't find one already set in the process
environment — unconditionally, not gated behind `if __name__ ==
"__main__"`. Any script or test that imports it in an unusual
environment can trigger this. See `docs/OPERATIONS_RUNBOOK.md`'s
"Known operational risk" section for the specifics and the incident
that surfaced this during this sprint.

## Global search scope (added this sprint)

`GET /api/search` covers committed jobs (any status) and still-pending
incoming messages — ticket, source, technician, city, service only, no
customer name/phone/address. Selecting a result for a `Closed`+`Settled`
or `Cancelled`/`Rejected` job navigates to the Completed/History screen
but does not yet pre-select that specific row there (no per-job
selection API exists for that screen yet) — every other case (Active,
Closings, still-pending Jobs) does pre-select. A minor, disclosed gap,
not a defect.

## Technician mapping completeness (found this sprint, not fixed)

A `python tools/system_readiness.py` run against the real database
found 12 of 15 active technicians missing a `dispatch_group` and/or
`closing_group` — the WhatsApp destinations a dispatch/closing gets
copied to. Not modified (this is real configuration data, not
something to alter without the owner). Confirm every technician who
will receive real dispatches during a pilot has both configured.
