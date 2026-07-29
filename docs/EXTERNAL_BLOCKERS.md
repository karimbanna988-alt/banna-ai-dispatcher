# External Blockers

Things this assistant cannot resolve by writing code -- each needs a
decision, a credential, or a piece of real business information from
the user (or from AGN/Lucky/Main Leads/Quo/WhatsApp themselves). Where
access is missing, the system builds a **preview/manual workflow only**
-- it never fabricates a substitute.

## 1. WhatsApp dispatch-group destination strings

**Blocked.** All 15 real technicians have a blank `dispatch_group`.
Every "Open WhatsApp" and "Mark Dispatched" control in `luxor_desk` is
correctly **hidden** (not just disabled) for every real job right now,
because there is nowhere real to hand it off to.

**What's needed from you:** for each active technician, the real
WhatsApp group name/link they should be dispatched to for AGN/Lucky/Main
Leads jobs in their coverage area. Once provided, these get stored via
the existing `technicians.dispatch_group` column (already in the real
schema) -- no code change needed, just real data entry through the
existing Technicians management screen (`luxor_ops.py`) or a direct,
reviewed data-entry pass.

## 2. Closing-group destination strings

**Blocked**, same shape as #1 but for `technicians.closing_group` (where
a technician's closing/payment confirmation should be routed). Currently
blank for all 15 technicians.

## 3. Verified Quo send-message API contract

**Blocked.** `dispatcher/outbound.py`'s approval-mode verification chain
(allowlist, approver, exact-text check, already-acknowledged check) is
fully implemented and tested, but `_send_live()` unconditionally raises
`NotImplementedError` -- there is no code path in this build that can
send anything to Quo. Before that could ever change:

- The exact real `POST /v1/messages` request/response contract needs to
  be confirmed against Quo's real API (the read-only probe script,
  `scripts/quo_probe_readonly.py`, only ever issued `GET` requests this
  session -- sending was never exercised, on purpose).
- Rate limits and error-handling behavior for a real send need
  confirming.

## 4. Explicit approval before enabling any Quo write

**Blocked by design, not by missing information.** `QUO_OUTBOUND_MODE`
must stay unset (disabled) in the real `.env` until you explicitly ask
for it to change. This assistant will not set it to `preview`,
`approval`, or `live` on its own initiative, and did not do so at any
point this session.

## 5. Official WhatsApp API access (only if automatic sending is ever wanted)

**Not pursued, and not needed for the supervised MVP.** The current
design is deliberately manual: copy the Luxor message, open WhatsApp
yourself, send it yourself. If automatic WhatsApp sending is ever
desired, that requires WhatsApp Business Platform / Cloud API
onboarding (a real Meta-side business verification process, external to
this codebase) -- out of scope until explicitly requested.

## 6. Missing source-specific acknowledgment wording

**Partially blocked.** AGN and Lucky both currently require a plain "K"
acknowledgment per `dispatcher/policy.py` (confirmed, tested,
unchanged). If either company's real required acknowledgment wording is
different from a bare "K" in some scenario not yet covered by
`docs/company_templates.md`, that's real business information only you
(or the company) can confirm -- nothing in the current fixtures
suggested otherwise, but the fixture set is necessarily a sample, not
exhaustive.

## 7. Technician-status and closing-entry workspace gap (see FINAL_MVP_PLAN.md M6/M7)

Not an external blocker in the usual sense, but worth listing here
because it currently limits what you can do from `luxor_desk` alone:
recording a technician's status update or entering a new closing still
requires the original `app.py`. This is an internal engineering gap
(scoped, not forgotten), not something that needs anything from you --
listed here for completeness since it affects what "real job test"
looks like today (see M8's exact procedure in the final report).
