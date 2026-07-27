# Luxor Locksmith — AI Dispatcher Workflow

Status: confirmed with the business owner during the requirements interview (2026-07-26). This is the source of truth for business behavior. Update this file (not just code) whenever a rule changes.

## 1. Job sources (Phase 1 = Quo only)

| Source | K reply required? | Updates required? | Closing sent back? |
|---|---|---|---|
| Main Leads (own ads) | No | No | No |
| AGN (outside company) | Yes, immediately on acceptance | Yes | Yes, AGN format |
| Lucky Locksmith LLC (outside company) | Yes, immediately on acceptance | Yes | Yes, Lucky format |

Other companies communicate directly via WhatsApp — explicitly out of scope for Phase 1.

Main Leads gives the customer's real phone number directly. AGN/Lucky sometimes give a company number plus an extension instead of the customer's direct number.

## 2. Job lifecycle

1. Raw job message arrives in Quo.
2. Detect source (Main Leads / AGN / Lucky / Unknown).
3. Extract structured fields (see section 4).
4. Check business hours, the permanent service-area/vehicle/restriction rules, each area's recurring weekly day off, then any random daily exceptions entered for today (see section 9b).
5. Decide accept / pass / needs-review (see section 5). Never decide on incomplete or conflicting data — flag it instead.
6. If accept and source is AGN/Lucky: prepare a "K" reply (never sent automatically).
7. Look up the correct regional WhatsApp dispatch group from the service-area mapping. If the location isn't in the mapping, do not guess — flag for the user.
8. Convert the job into the Luxor Locksmith technician template (never expose the original company's raw format to the technician).
9. Present everything on the approval screen. Human approves, edits, rejects, or handles manually. Nothing is sent automatically in Phase 1.
10. Later: technician status updates (manually pasted by the dispatcher in Phase 1) get turned into short standard replies back to the company.
11. Eventually: closing information from the technician is repeated (never altered) into the company-specific closing template and sent back.

## 3. Real company templates (see `docs/company_templates.md` for masked field-by-field examples)

- **Main Leads**: `Ticket`, `Name`, `Phone`, `2nd Phone #`, `Address`, `Desc` (service + vehicle often together), plus a freeform note line.
- **AGN**: `Ref`, `PDL`, `Ph` (often `company number (Job Ext: NNN)`), `Addr`, `Desc`, `Occu`, `Notes`. No dedicated customer-name field. Vehicle info, if present, is embedded in `Desc`/`Notes`.
- **Lucky**: `Confirm` link, `Co`, `PDL`, `Ph`, `Addr`, `Desc`, `Occu`, `Notes`. Same shape as AGN; extension not always present.

## 4. Fields to extract per job

- Detected source company (Main Leads / AGN / Lucky / Unknown)
- Ticket / PDL number
- Customer name (if provided — AGN/Lucky often omit it)
- Phone number
- Second phone number, if provided (confirmed 2026-07-27 — a real customer-supplied alternate contact number, never dropped just because it's optional)
- Extension, if provided — either wrapped as `(Job Ext: N)` or a bare trailing number after the phone (e.g. "7048000948 133", confirmed 2026-07-27); never invented — see section 6
- Full address
- City, state, ZIP
- Service type
- Vehicle info (make/model/year), if present. Year may be written as a 2-digit shorthand (e.g. "Accord 12" meaning 2012) — recognized via the convention 00-69 → 20XX, 70-99 → 19XX (confirmed 2026-07-27), not treated as missing.
- Price or company range — **never extracted as a number to act on**; if present in text it's surfaced as raw text only, and any price question always escalates to the human (Phase 1 has no pricing logic at all)
- Notes / special instructions

## 5. Accept / Pass / Needs-review logic

Accept (and prepare "K" for AGN/Lucky) only when **all** of the following hold:
- The current time is within business hours (see section 9b).
- The area is active, its dispatch/closing group is confirmed, and today isn't its recurring day off (or an explicit daily exception says it's open anyway).
- A technician is available in that region (not off, not busy, not unknown -- see section 9b for the current "available by default" testing placeholder).
- The service type is not restricted.
- The vehicle (if identified) is not restricted — see the vehicle rules below.

Pass when any of:
- Area is outside today's coverage.
- No technician available in that region.
- The regional technician is off or already busy with another job.
- Service type or location is not one Luxor Locksmith handles.

**Needs-review / Handle Manually** (never silently accept or pass) when:
- A required field is missing, unclear, or conflicting (see `docs/phase1_scope.md` for the exact trigger list).
- The vehicle doesn't clearly match a known restriction rule.
- The location isn't found in the service-area mapping.
- A price question or dispute/complaint appears.
- Anything else that would otherwise require guessing.

### Vehicle restrictions (current — stored as data in `rules/vehicle_restrictions.json`, not code, because these change over time)
- No vehicles roughly mid-1980s or older.
- No Audi. No Volkswagen.
- Mercedes generally accepted up to ~2011.
- BMW generally accepted up to ~2014.
- Anything ambiguous → ask the human, don't decide.

## 6. Phone number & extension handling

- Pass phone number **and** extension through to the technician exactly as given — never altered, removed, or invented.
- Never fabricate an extension. The real phone-tracking/routing system (private number + auto-generated extension) is a later phase and does not exist yet. Until then, whatever the company/Quo message provides is what gets shown — nothing more.

## 7. Pricing

- Phase 1: the AI **never** calculates, infers, or suggests a price. Any price question or price-related ambiguity always escalates to the human.
- Later phase (not Phase 1): AI may learn per-company rate sheets and suggest a price, but only as a suggestion requiring explicit human approval before anything is sent — never auto-sent.

## 8. Status updates & company questions

Standard short replies, used only from a **confirmed** technician status (never invented):

| Technician says | Reply to company |
|---|---|
| Going | Going |
| ETA 20 min | Tech ETA 20 min |
| No answer | No answer |
| On phone | Tech on phone |
| Pass | Pass |
| Job completed | Send closing in the company's specific format |

- "Any update?" → reply with the latest confirmed technician status. No confirmed status → flag and ask the human to check with the technician.
- "Did anyone call?" → means: did **our technician** call the customer (not the reverse). Answer only from a confirmed tech update ("Tech called", "Tech calling now", "No answer", "Not yet"). No confirmed status → flag and ask.
- Phase 1 has no live WhatsApp connection: the dispatcher manually pastes the technician's latest status, and the AI drafts the Quo reply for approval.

## 9. Closings

- Closing = original job info (as received) + amount + payment method (e.g. "CC" = credit card) + parts cost, repeated **exactly** as the technician reports it. Never recalculated, altered, or invented.
- AGN and Lucky each have their own closing template — stored separately, never assumed identical.

## 9b. Business hours and area availability (confirmed 2026-07-27, supersedes earlier daily-open/closed assumption)

- The business operates every day, **8:00 AM - 10:00 PM America/New_York**, computed via the IANA timezone (never a hardcoded offset from the operator's local time -- daylight saving changes the conversion). Outside these hours, every decision is `HANDLE_MANUALLY` with reason "Outside operating hours."
- There is **no daily "area open/closed" message** in real operation. Every configured service area is active by default, permanently.
- Some areas have a **recurring weekly day off** (e.g. Chicago: Saturday; Raleigh, NC: Sunday) -- stored in `rules/service_areas.yaml`, not daily messages. Hitting a recurring day off resolves to `PASS` or `HANDLE_MANUALLY` depending on that area's configured `recurring_day_off_action`. The full weekly schedule isn't known yet; unconfirmed areas simply have no recurring day off configured.
- The **only thing that varies day to day** is: a technician taking a random day off or being busy, a one-off area becoming temporarily unavailable, or a temporary vehicle/service restriction (e.g. "no BMW today") -- all entered manually for that day, all optional. If nothing is entered, the system falls back to the area being active and the technician being available (see below).
- For now, `default_technician_availability` is set to "available" for every configured area, as an explicit temporary testing default -- **not** a claim that a specific technician is actually confirmed. This will be replaced by a real technician-availability workflow later.

## 10. Dispatch routing

- Fixed city/county/service-area → regional WhatsApp dispatch group mapping (e.g. Columbia, SC → Columbia group; Charlotte, NC → Charlotte group). Each dispatch group has a matching Closing group.
- Unknown location → ask the human, never guess.
- Permanent service-area data lives in a separate reference document (see `docs/phase1_scope.md` for the planned schema) — not hardcoded, built as its own deliverable later.
- Areas are active by default, permanently (see section 9b) -- there is no daily area open/closed message. Random daily exceptions (technician off/busy, one-off area unavailability, temporary restrictions) may optionally be entered by the dispatcher, parsed into structured rules that overlay the permanent/recurring model for that day, shown back as a confirmation summary, and expire after a shift unless extended. Unclear/conflicting entries → ask, never guess.

## 11. Technician-facing message

- There is currently no standardized technician message; today the dispatcher copy-pastes the original company text as-is.
- Every job (any source) gets converted into one consistent **Luxor Locksmith**-branded technician template before being shown for approval. The technician never sees the original AGN/Lucky/Main Leads formatting.
- The original source company is still tracked internally (drives K-reply logic, update routing, and which closing template applies) even though it's hidden from the technician-facing message.
- **The exact Luxor Locksmith template is not finalized yet** — to be designed with the business owner. Phase 1 code uses a clearly-marked placeholder template until then.

## 12. Duplicate / job-record handling

- Ticket/PDL number is the job's ID, but the same ID can recur (corrections, update requests, tech status, closing, cancellation) — repeats are not automatically duplicates to ignore.
- One job record per ticket/PDL; later messages attach to the existing record and get classified as: new job / duplicate copy / corrected info / update request / tech status update / closing / cancellation.
- Never send the same K, Pass, update, or closing twice.
- Full timeline of all messages and actions is kept per job (see audit log in `docs/phase1_scope.md`).
- A repeat message with materially different info than the original → flag for human review, never silently overwrite.

## 13. Human approval

Every AI output is a **suggestion only**. Nothing is sent to Quo or WhatsApp automatically in Phase 1. The human can Approve, Edit, Reject, or Handle Manually every suggested action.

Escalate to the human whenever:
- Price is requested or unclear.
- A complaint, refund, or dispute appears.
- The address can't be identified confidently.
- Service coverage is uncertain.
- Information conflicts.
- A technician update is missing.
- The customer or company seems angry.
- The request is unusual.
- The AI has low confidence (see confidence scoring in `docs/phase1_scope.md`).
- A response could create financial or operational risk.
