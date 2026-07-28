# Phase 1 Scope & Architecture

## Goal

A local, no-live-connections prototype: paste a raw Quo job message in, get a full breakdown and suggested actions out, approve/edit/reject/handle manually. Nothing is ever sent to Quo or WhatsApp automatically.

## Tech stack (confirmed with business owner)

- **Python** for parsing, rules, and formatting logic.
- **Streamlit** for the local browser-based approval interface.
- **JSON/YAML** files for business rules (vehicle restrictions, service-area mapping, company templates) so they can be edited without touching code.
- **SQLite** for job history and the audit log.
- **pytest** for the test suite.

## Module separation

The system is split into independent layers so Quo, WhatsApp, and a future mobile interface can be connected later without rebuilding the core:

```
src/dispatcher/
  parser.py       # raw text -> detected source + extracted fields + confidence
  models.py       # shared data classes (Job, ExtractedFields, ConfidenceReport, ...)
  closing.py      # renders a company's closing message from a confirmed tech report
  companies/       # one profile module per company -- see "Company profile architecture" below
    base.py         # CompanyProfile / ClosingTemplate shapes
    main_leads.py
    agn.py
    lucky.py
    __init__.py     # registry: Source -> CompanyProfile
  rules/           # accept/pass/needs-review/handle-manually decision layer
    __init__.py      # decide(job, profile, service_areas, daily_rules) -> Decision
    service_areas.py # permanent service-area lookup (rules/service_areas.yaml)
    daily.py         # freeform daily shift-message parser -> DailyRules
  formatter.py    # Luxor Locksmith technician message formatting (Version 1 rule)
  updates.py      # technician status text -> standard company reply
  storage.py      # SQLite job history + audit log + message classification
app.py            # Streamlit approval interface
rules/            # JSON/YAML rule data (not code): vehicle_restrictions.json, service_areas.yaml
docs/             # this documentation
tests/            # pytest suite
```

**Status: all of the above is built.** See `docs/architecture.md` for the full end-to-end flow, data contracts, and the list of placeholders/assumptions made where a business fact (dispatch/closing group names, a real AGN closing example, the final Luxor template design) was still missing.

## Company profile architecture

Every company-specific behavior (K required?, closing template, header line) lives in its own module under `src/dispatcher/companies/` — `main_leads.py`, `agn.py`, `lucky.py`, and one file per future company. `src/dispatcher/companies/__init__.py` just registers each module's `PROFILE` in a lookup keyed by `Source`. Adding or changing one company means adding/editing one file — it cannot accidentally affect another company's profile.

The closing format itself is **shared by default** (`SHARED_CLOSING_TEMPLATE` in `companies/base.py`) — confirmed by the business owner on 2026-07-26 as the same shape across AGN, Lucky, and other outside companies today. Each company's profile just supplies its own `closing_header_line` (its own Quo job header, e.g. "Co: LUCKY LOCKSMITH LLC" vs "Ref: AGN Locksmith Services") as data, not logic. A company only gets its own distinct `ClosingTemplate` if its real format is later found to genuinely differ — that's a one-file change (`tests/test_closing.py::test_company_profile_can_override_the_shared_template` proves the override mechanism works). Current state:
- **Lucky**: `closing_supported=True`, uses `SHARED_CLOSING_TEMPLATE`, header line "Co: LUCKY LOCKSMITH LLC" (the one real example this template was modeled on, confirmed 2026-07-26).
- **AGN**: `closing_supported=True`, uses `SHARED_CLOSING_TEMPLATE`, header line "Ref: AGN Locksmith Services". No real AGN closing example has been seen yet — this assumes AGN follows the same shared shape, per the business owner. If a real AGN closing later shows a different shape, give AGN its own `ClosingTemplate`.
- **Main Leads**: `closing_supported=False` — Main Leads jobs never get a closing sent back to a company at all (this is unrelated to the shared-template question; Main Leads simply has no closing step).

## Confidence scoring

Every extraction produces a confidence score (0–100) **per field** and an overall job confidence, with human-readable reasons — never just a bare number.

Rules of thumb:
- A field matched by an exact, unambiguous pattern (e.g. a clearly labeled `PDL: XXXXX`) → high confidence.
- A field inferred from a freeform area (e.g. vehicle info guessed out of a `Notes:` sentence) → medium confidence, reason noted (e.g. "vehicle inferred from free text, not a labeled field").
- A field missing entirely, or matched by more than one conflicting candidate → low confidence, reason noted, and it contributes to a Handle Manually trigger (see below).
- The **overall job confidence** is not a simple average — any single low-confidence required field caps the overall score and drives the status to Handle Manually.

## Automatic "Handle Manually" triggers

The job status is automatically forced to `HANDLE_MANUALLY` (never silently downgraded to a lower-visibility state) when any of the following are true:

- A required field (ticket/PDL, phone, address, service type) is missing entirely.
- A field has two or more conflicting candidate values (e.g. two different addresses found in the text).
- The detected source is `UNKNOWN` (no template fingerprint matched confidently).
- The vehicle doesn't clearly match a known restriction rule.
- Overall confidence falls below the configured threshold.

This status is a hard override at the extraction layer — even if the rest of the job looks acceptable, `HANDLE_MANUALLY` always wins over an accept/pass suggestion. `rules.decide()` (in `src/dispatcher/rules/__init__.py`) adds its own hard-override checks on top of this: the job's location not being found in the service-area mapping, its dispatch/closing group being unconfirmed (true for every area in `rules/service_areas.yaml` today), or no daily rules having been provided for the session all also force `HANDLE_MANUALLY`. See `docs/architecture.md` section 3 for the full ordered list of checks.

## Audit log

Every job processed gets a full, append-only audit trail stored in SQLite, containing at minimum:
- The original raw message (verbatim).
- The AI's full output (detected source, extracted fields, confidence report, suggested status/reply).
- Every edit the human makes before approving.
- The final action taken (Approve / Edit / Reject / Handle Manually) and timestamp.

Implemented in `storage.py`: a `jobs` table (latest field snapshot per ticket, for comparison) and an append-only `events` table (one row per processed message — original raw message, extracted fields, confidence, decision, suggested reply, Luxor message, dispatcher edits, final action, timestamp). Past `events` rows are never updated or deleted, so the original message and every past decision stay intact even when a later message corrects the same ticket.

## Test suite

`tests/` uses masked, clearly-fabricated sample jobs (not real customer data) mirroring the structures in `docs/company_templates.md`, covering:
- Correct source detection for Main Leads, AGN, Lucky, and an unrecognized/unknown format.
- Correct field extraction for a clean, complete job of each type.
- Confidence scoring: high confidence on clean fields, low confidence + reason on missing/ambiguous ones.
- `HANDLE_MANUALLY` auto-triggering on missing required fields, conflicting fields, and unknown source.
- The full decision engine: accept, pass (closed area, inactive area, busy/off technician, restricted vehicle, daily temporary restriction), needs-review (borderline confidence), and every handle-manually trigger (area not found, group unconfirmed, no/expired daily rules, unclear vehicle).
- Message classification in storage: new, duplicate, correction (with conflicting-field detection), status, closing, cancellation, and that the original message is never overwritten.
- The Luxor Locksmith formatter never leaks source-company branding and preserves every field verbatim.
- The technician status/company-question mapper, including the "did anyone call" / "any update" fallback to "no confirmed status" when nothing is known.

221 tests passing as of the last full run (2026-07-28) -- see
`docs/architecture.md` sections 5, 8, and 9 for the simulator, receive-only
Quo integration, and Phase 1 CRM foundation work added since this count
was last 69.

## Open items carried over from the interview

- Exact Luxor Locksmith technician message template — Version 1 rule is implemented (`src/dispatcher/formatter.py`), but the business owner hasn't reviewed the actual field order/labels yet.
- Full permanent service-area mapping — `rules/service_areas.yaml` has 13 named areas as a placeholder (the original 12 plus Raleigh, NC, added 2026-07-27), but every dispatch/closing group is unconfirmed; this forces `HANDLE_MANUALLY` for every job today until real group names are filled in.
- AGN's closing template — confirmed 2026-07-27 to share Lucky's shared format against a real AGN closing example (ticket UQ9DI, `$690 Cash`); see `docs/architecture.md` section 4.
