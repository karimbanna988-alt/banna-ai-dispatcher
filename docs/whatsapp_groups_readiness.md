# WhatsApp Groups API Readiness — Factual Audit

Status: **Investigation only. No account changes, no tokens generated, no
groups created, no messages sent.** This document describes what exists
today and what a future, explicitly-approved integration phase would need
to do. It is not a build plan for this phase and nothing here has been
implemented beyond the existing manual-handoff adapter described in
Section 5.

## 1. Does this project have any official WhatsApp Cloud API configuration today?

**No.** A repository-wide search for the standard Meta/WhatsApp Cloud API
configuration variable *names* (Meta App ID, WhatsApp Business Account ID,
Phone Number ID, access token, webhook verify token, app secret, callback
URL) found none, in code or in `.env.example`. No values were printed or
needed to reach this conclusion — the search was for variable names only.

| Configuration item | Present? |
|---|---|
| Meta App ID | Missing |
| WhatsApp Business Account (WABA) ID | Missing |
| Phone Number ID | Missing |
| Access token | Missing |
| Webhook verify token | Missing |
| App secret | Missing |
| Callback/webhook URL configuration | Missing |

The only WhatsApp-related identifiers anywhere in the codebase are two
internal enum labels (`WHATSAPP_TECHNICIAN`, `WHATSAPP_COMPANY`) used by
the Shadow Mode audit trail (`src/dispatcher/shadow.py`) to tag which
channel a *manually sent* message went through. These are not API
credentials and do not indicate any Cloud API integration exists.

The project's actual `.env` currently defines only `LUXOR_DEMO_PASSWORD`,
`QUO_API_KEY`, and `QUO_POLL_INITIAL_LOOKBACK_MINUTES`. Nothing
WhatsApp-related is defined there either.

**Conclusion**: this is a clean slate. No prior WhatsApp Cloud API setup
to build on, and no prior setup to accidentally break.

## 2. Does Official Business Account eligibility still need confirmation?

**Yes — unresolved, and this document does not resolve it.** Using the
WhatsApp Cloud API (including the Groups-adjacent capabilities described
below) requires:

- A Meta Business Account, verified.
- A WhatsApp Business Account (WABA) linked to that Business Account.
- A registered phone number under that WABA (this can be a new/test
  number — see Section 4 — but it still has to go through Meta's
  registration and, for production use, business verification).
- The app to be reviewed/approved for the relevant permissions if used
  beyond a small allow-listed test audience.

None of this has been confirmed, started, or assumed in this codebase.
Nothing in the repository asserts eligibility, and this document should
not be read as implying eligibility is likely or close — only that it is
the next fact to establish, outside of this codebase, before any of
Section 4's steps could run for real.

## 3. Ordinary WhatsApp groups vs. API-managed groups

The technician "dispatch group" and "closing group" values already
present in this project's data (e.g. `Technician.dispatch_group`,
`Technician.closing_group` in the real schema, and the same fields in the
`operator_ui_v2` fabricated prototype data) are **ordinary WhatsApp
groups** — created and administered the normal way, in the regular
WhatsApp app, with no API involvement.

**These must not be assumed importable into, or manageable by, the
official WhatsApp Cloud API.** The Cloud API's group-messaging surface
(where available) operates on groups it created and manages itself; it
does not take ownership of a pre-existing consumer WhatsApp group, and
there is no supported "claim this existing group" operation. Any future
integration would need its own, separately created API-managed group(s)
— never a silent assumption that today's `Kabaka Dispatch` or `Ismail
Columbia Dispatch` groups could simply start being driven by the API.

## 4. Controlled test sequence (proposed, not executed)

This is the sequence a future, explicitly-approved phase would follow.
None of these steps have been run. Each step requires human approval
before the next one starts.

1. **Confirm account eligibility** — verify the Meta Business Account and
   WABA are in good standing and reviewed for the permissions needed
   (outside this codebase; requires access to Meta Business Manager).
2. **Use a non-production/test number where possible** — Meta provides
   test phone numbers for exactly this purpose during development, so the
   real dispatch number is never put at risk during testing.
3. **Create one API-managed test group** — a single group, created
   through the API, not a converted existing group.
4. **Generate an invitation link** for that test group.
5. **Invite one technician** (a real person, but told explicitly this is
   a test) to join via that link.
6. **Receive one fabricated test message by webhook** — requires a
   reachable public webhook endpoint, which does not exist yet (see
   Section 6).
7. **Prepare one response** — drafted, not sent.
8. **Require human approval** before sending anything.
9. **Send one approved test response** — the first and only real network
   send in this entire sequence, and only after step 8's explicit human
   approval.
10. **Record the official group ID** returned by the API, for use by any
    future integration code.

Every step above is gated on the previous one and on human approval;
nothing here proceeds automatically or unattended.

## 5. Existing Python integration point for a future adapter

A future Cloud API integration already has a clean, pre-built seam to
plug into: [`src/dispatcher/adapters.py`](../src/dispatcher/adapters.py).

- `WhatsAppHandoffAdapter` (a `typing.Protocol`) defines the two
  operations any WhatsApp integration needs: `prepare(text, destination)
  -> WhatsAppDispatchInstruction` and `confirm_sent(conn, *, job_id,
  technician_id) -> None`.
- The only implementation today is `ManualWhatsAppHandoffAdapter` —
  `prepare()` just formats the message and reports whether a destination
  is configured; `confirm_sent()` records the dispatcher's own manual
  confirmation via `storage.record_whatsapp_handoff()`. It never calls
  any WhatsApp API and never claims delivery on the operator's behalf.
- `DispatcherService` takes this adapter as a constructor argument
  (`whatsapp_adapter=...`, defaulting to `ManualWhatsAppHandoffAdapter()`
  in `application.py`), specifically so a future
  `CloudApiWhatsAppHandoffAdapter` could be substituted later without
  changing `DispatcherService` or any UI code that calls it — the
  interface is already the abstraction boundary.

No such Cloud API adapter exists yet. This section describes where it
would go, not that it has been started.

## 6. What remains manual if Groups API eligibility is unavailable

If Business Account/Groups eligibility cannot be obtained, or the review
process stalls, the following stays exactly as it works today —
`ManualWhatsAppHandoffAdapter` is already a complete, working design for
this case, not a placeholder waiting to be replaced:

- The dispatcher copies the prepared technician/company message text
  themselves.
- The dispatcher opens WhatsApp themselves (their own phone or WhatsApp
  Web) and sends it to the correct existing group themselves.
- The dispatcher taps "Confirm sent" in the app afterward, which is the
  only thing the app records — a human attestation, never an API
  delivery receipt.
- No incoming technician replies are captured automatically; the
  dispatcher reads them in WhatsApp directly and, where relevant, keys
  the resulting status back into the app manually (e.g. "Add technician
  update" in the Active screen).

This manual path also has no ongoing dependency on Meta approval status,
API changes, or account eligibility — it keeps working regardless of how
Section 2 resolves.

## 7. Why unofficial WhatsApp Web automation is excluded

Browser automation against WhatsApp Web (e.g. Selenium/Puppeteer driving
a logged-in WhatsApp Web session, or unofficial libraries that do the
same) was considered and is deliberately excluded from this project's
design, for reasons independent of whether the Cloud API path succeeds:

- **Terms of Service risk**: WhatsApp's terms prohibit unauthorized
  automated/bulk use of the consumer product; this risks the number
  being banned, which for a working locksmith dispatch line is a
  business-continuity risk, not just a technical one.
- **No delivery guarantees**: unofficial automation has no SLA, breaks
  silently on WhatsApp Web UI changes, and offers no supported webhook
  for inbound messages — every "read incoming replies" path would itself
  be more browser automation, compounding the risk.
- **Session/credential fragility**: it requires keeping a real WhatsApp
  account's web session persistently authenticated on a server, which is
  both a security exposure (session hijack risk) and an operational one
  (silent logout requiring re-scanning a QR code).
- **Detection evasion is explicitly out of scope**: any technique aimed
  at making automated use of WhatsApp Web look like manual human use to
  avoid detection is not something this project will build, independent
  of the business risk above.

The existing `ManualWhatsAppHandoffAdapter` design (Section 6) was chosen
specifically because it needs none of this: it never automates WhatsApp
in any form, so none of these risks apply to the current system.

## Safe local readiness-check tool

A companion script, `scripts/whatsapp_readiness_check.py`, checks only
whether the environment-variable *names* listed in Section 1 are present
in the current environment (via `os.environ`) — never their values. See
that script's own header for exact usage. Running it does not require or
assume any of the variables exist; it is safe to run at any time,
including right now, and prints only found/missing per name.
