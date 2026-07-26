# Phase 1 Architecture — Complete Execution Flow

**Status: Phase 1 core system is built and tested (69 pytest cases passing).**
This is the full design for Phase 1: everything from a raw Quo message landing
in the dispatcher's clipboard to the dispatcher pressing **Approve** or
**Handle Manually**. Every module below is now `[DONE]`; where a real business
fact was still missing (dispatch/closing group names, the exact Luxor
template's finer points, a real AGN closing example), the module was built
against a clearly-marked, safe, editable placeholder instead of pausing —
see section 4 for the full list of those placeholders.

## 1. End-to-end flow for one job

```
 Dispatcher                         app.py (Streamlit)                    Core modules
 ───────────                        ──────────────────                    ─────────────

 Pastes raw Quo   ──────────────▶   Process button clicked
 message text                            │
                                          ▼
                                   parser.extract_job(raw_text, vehicle_rules)
                                          │
                                          ▼
                                   ExtractedJob
                                   (source, ticket, phone, ext, address,
                                    city/state/zip, service_type, vehicle,
                                    notes, per-field confidence + reasons,
                                    overall_confidence, status,
                                    status_reasons, vehicle_restriction_*)
                                          │
                                          ▼
                                   storage.classify_incoming(conn, job)
                                          │
                              ┌───────────┴───────────┐
                        looks up existing         no existing record
                        record for this           for this ticket/PDL
                        ticket/PDL in SQLite              │
                              │                           │
                              ▼                           ▼
                     MessageType: DUPLICATE /       MessageType: NEW, or
                     CORRECTION / UPDATE_REQUEST /   CLOSING_WITHOUT_DISPATCH
                     STATUS / CLOSING / CANCELLATION /  if a closing-looking
                     UNRECOGNIZED                    message has no prior
                              │                      record for its ticket
                              │                      (forces HANDLE_MANUALLY
                              │                      via force_handle_manually)
                              └───────────┬───────────────┘
                                          ▼
                                   companies.get_company_profile(job.source)
                                          │
                                          ▼
                                   CompanyProfile
                                   (requires_k, closing_supported,
                                    closing_template, closing_header_line)
                                          │
                                          ▼
                                   rules.decide(job, profile,
                                                 service_areas, daily_rules)
                                          │
                                          ▼
                                   Decision
                                   (verdict: ACCEPT / PASS / NEEDS_REVIEW /
                                    HANDLE_MANUALLY, confidence, reasons,
                                    missing_information, conflicts,
                                    restrictions_triggered, dispatch_group,
                                    quo_reply_suggestion)
                                          │
                                          ▼
                                   formatter.format_technician_message(job)
                                          │
                                          ▼
                                   Luxor Locksmith technician message (string)
                                          │
                                          ▼
                    ◀──────────── Approval screen rendered:
                                  - original raw message
                                  - detected source + confidence
                                  - extracted fields + confidence + reasons
                                  - HANDLE MANUALLY banner (if forced) + why
                                  - decision verdict + reasons + missing/
                                    conflicts/restrictions
                                  - dispatch group (or "not confirmed")
                                  - Luxor-formatted technician message (editable)
                                  - suggested Quo reply (K / none)
                                  - duplicate/conflict warnings from classify_incoming
                    │
        Approve / Edit /
        Reject / Handle
        Manually  ────────────▶   storage.save_audit_entry(
                                       conn, job, classify_result, decision,
                                       suggested_quo_reply, luxor_message,
                                       dispatcher_edits, final_action)
                                          │
                                          ▼
                                   SQLite: jobs row (latest snapshot) +
                                   events row (append-only, original
                                   message never overwritten)
                                          │
                                          ▼
                    ◀──────────── Nothing sent anywhere automatically.
                                  Dispatcher copies the approved text
                                  into Quo / WhatsApp themselves.
```

Three side flows use the same core modules but enter the pipeline differently
(all still end at a human approval step, never auto-sent):

- **Daily shift setup** (Daily Rules tab) → dispatcher pastes the freeform
  "today's areas/techs" message once per shift → `rules/daily.py` parses it
  into `DailyRules`, shown as a confirmation summary → held in the Streamlit
  session and fed into every `rules.decide()` call until it expires.
- **Technician status update** (Status Update tab) → dispatcher pastes the
  tech's confirmed status text → `updates.map_status_to_reply()` maps it to
  the standard short reply (also used to answer "Any update?" / "Did anyone
  call?" via `updates.answer_from_latest_status()`) → shown for approval.
- **Closing** (Closing tab) → dispatcher enters amount / payment method /
  parts (verbatim from the technician) → `closing.render_closing(job,
  ClosingInput)` → shown for approval → recorded to the audit log as a
  `CLOSING` event.

## 2. Module inventory

| File | Responsibility | Input | Output | Status |
|---|---|---|---|---|
| `src/dispatcher/models.py` | Shared dataclasses (`FieldValue`, `ExtractedJob`, `Source`, `ExtractionStatus`) | — | — | **DONE** |
| `src/dispatcher/parser.py` | Detect source, extract every field, score confidence, check vehicle against restriction data, force `HANDLE_MANUALLY` | raw Quo text + vehicle rules dict | `ExtractedJob` | **DONE** |
| `rules/vehicle_restrictions.json` | Vehicle restriction data (editable without touching code) | — | — | **DONE** |
| `src/dispatcher/companies/base.py` | `CompanyProfile` / `ClosingTemplate` shapes + `SHARED_CLOSING_TEMPLATE` | — | — | **DONE** |
| `src/dispatcher/companies/{main_leads,agn,lucky}.py` | One company's `requires_k`, closing support, header line | — | `PROFILE` | **DONE** |
| `src/dispatcher/companies/__init__.py` | Registry: `Source → CompanyProfile` | `Source` | `CompanyProfile` | **DONE** |
| `src/dispatcher/closing.py` | Render a company's closing message from a confirmed tech report | `ExtractedJob`, `ClosingInput` | `ClosingResult` | **DONE** |
| `rules/service_areas.yaml` | Permanent service-area data (12 known areas; dispatch/closing groups intentionally left unconfirmed) | — | — | **DONE (placeholder data)** |
| `src/dispatcher/rules/service_areas.py` | Look up city/state in the permanent service-area mapping | city, state, area list | `ServiceAreaLookup` (confirmed/unconfirmed/inactive/not_found) | **DONE** |
| `src/dispatcher/rules/daily.py` | Parse the dispatcher's freeform shift-start message into structured overrides | freeform text | `DailyRules` | **DONE** |
| `src/dispatcher/rules/__init__.py` | Accept/pass/needs-review/handle-manually decision, combining job status + area + daily overrides + vehicle/service restrictions | `ExtractedJob`, `CompanyProfile`, service areas, `DailyRules` | `Decision` | **DONE** |
| `src/dispatcher/formatter.py` | Convert `ExtractedJob` into the Luxor Locksmith-branded technician message (V1 rule) | `ExtractedJob` | technician message (string) | **DONE** |
| `src/dispatcher/updates.py` | Map a technician's confirmed status text to the standard short company reply | freeform tech status text | `StandardReply` | **DONE** |
| `src/dispatcher/storage.py` | SQLite job records; classify incoming messages against existing records; append-only audit log | `ExtractedJob` + every stage's output + human actions | persisted rows | **DONE** |
| `src/dispatcher/rules/__init__.py::force_handle_manually` | Escalate an already-computed `Decision` to `HANDLE_MANUALLY` for cross-cutting overrides decided outside `decide()`'s own checks | `Decision`, reason | mutated `Decision` | **DONE** |
| `app.py` | Streamlit UI; orchestrates every module above; Approve/Edit/Reject/Handle Manually + Daily Rules/Status/Closing/Audit tabs | dispatcher input | approval screen + writes to storage | **DONE** |
| `tests/test_*.py` | Regression coverage across all of the above | — | — | **DONE** (69 tests passing) |

## 3. Key data contracts (as implemented)

```python
# src/dispatcher/rules/__init__.py
class Verdict(str, Enum):
    ACCEPT = "ACCEPT"
    PASS = "PASS"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    HANDLE_MANUALLY = "HANDLE_MANUALLY"

@dataclass
class Decision:
    verdict: Verdict
    confidence: int
    reasons: List[str]
    missing_information: List[str]
    conflicts: List[str]
    restrictions_triggered: List[str]
    dispatch_group: Optional[str] = None
    quo_reply_suggestion: Optional[str] = None

# src/dispatcher/rules/service_areas.py
@dataclass(frozen=True)
class ServiceAreaRecord:
    city: Optional[str]
    state: Optional[str]
    zip_codes: List[str]
    dispatch_group: Optional[str]
    closing_group: Optional[str]
    active: bool
    default_technician_availability: str
    company_restrictions: List[str]
    notes: str

@dataclass
class ServiceAreaLookup:
    status: str  # "confirmed" | "unconfirmed" | "inactive" | "not_found"
    record: Optional[ServiceAreaRecord]
    dispatch_group: Optional[str]
    closing_group: Optional[str]
    reason: str

# src/dispatcher/rules/daily.py
@dataclass
class DailyRules:
    raw_text: str
    open_areas: List[str]
    closed_areas: List[str]
    technician_status: Dict[str, str]
    temporary_restrictions: List[str]
    unparsed_lines: List[str]           # never guessed -- surfaced instead
    created_at: datetime
    expires_at: datetime
    extended: bool

# src/dispatcher/storage.py
class MessageType(str, Enum):
    NEW = "NEW"
    DUPLICATE = "DUPLICATE"
    CORRECTION = "CORRECTION"
    UPDATE_REQUEST = "UPDATE_REQUEST"
    STATUS = "STATUS"
    CLOSING = "CLOSING"
    CANCELLATION = "CANCELLATION"
    UNRECOGNIZED = "UNRECOGNIZED"

@dataclass
class ClassifyResult:
    message_type: MessageType
    existing_job_id: Optional[int]
    conflicting_fields: List[str] = field(default_factory=list)
    reason: str = ""
```

**Hard-override chain, in the order `rules.decide()` actually checks them:**
1. `job.status == HANDLE_MANUALLY` (set by `parser.py`: missing/conflicting required field, unrecognized source, unclear vehicle, or low overall confidence) → `Verdict.HANDLE_MANUALLY`, always, regardless of anything else.
2. Service area not found in the mapping → `HANDLE_MANUALLY`.
3. Service area found but its dispatch/closing group is unconfirmed → `HANDLE_MANUALLY` (never guesses a group name — this is the current state of every real entry in `rules/service_areas.yaml` today).
4. Service area marked inactive → `PASS`.
5. No daily rules provided for the session, or today's daily rules have expired → `HANDLE_MANUALLY` (can't confirm current coverage).
6. Area closed today / technician off or busy / vehicle restricted / a daily temporary restriction matches → `PASS`.
7. Vehicle restriction status is `"unclear"` (safety net; parser should already have caught this) → `HANDLE_MANUALLY`.
8. Otherwise: `ACCEPT` if `job.overall_confidence >= 80`, else `NEEDS_REVIEW` (job data was clean and extractable, but confidence is borderline — double-check before approving).

## 4. Placeholders and assumptions made to keep moving (per your instruction not to pause)

These are safe, editable, and clearly marked as TODO — none of them silently guess a business fact:

- **`rules/service_areas.yaml`**: the 12 named areas are in the file, but every `dispatch_group`/`closing_group` is `null` ("unconfirmed"). This means **every job today, in every one of those areas, currently resolves to `HANDLE_MANUALLY`** until you fill in real group names — see the file's own TODO comments. Several city names (Arlington, Augusta, Columbia, Greenville, Chicago, Vancouver) are ambiguous across multiple US states, so `state` was left blank for those rather than guessed; only "Charlotte, NC" had an explicit state.
- **AGN's closing format**: **confirmed 2026-07-27** against a real AGN closing example (ticket UQ9DI, `$690 Cash`) — same repeat-fields + "Closed" + amount + payment-method shape as Lucky's, just a different payment method ("Cash" vs "CC") and no parts, both already handled generically by `ClosingInput`. No template change was needed; this validates the shared-template design. See `tests/test_closing.py::test_agn_closing_against_real_confirmed_shape`.
- **AGN name field**: a related real bug was caught from that same example — AGN sometimes provides a customer name via a bare `N:` label, which the parser didn't look for at all (it had hardcoded "AGN/Lucky never have a name" from a single earlier sample). Fixed in `parser.py`: `NAME_RE` now matches `Name:` or `N:`, and name extraction is attempted for every source rather than skipped for non-Main-Leads.
- **Luxor Locksmith technician message template**: implements your Version 1 rule exactly (strip source branding, preserve every field verbatim, clean spacing only). It has not been visually reviewed by you yet — treat the current field order/labels in `src/dispatcher/formatter.py` as a first draft you can adjust.
- **`NEEDS_REVIEW_CONFIDENCE_THRESHOLD = 80`** in `src/dispatcher/rules/__init__.py`: an engineering placeholder distinguishing a clean `ACCEPT`/`PASS` from one that's technically decidable but worth a second look. Adjustable in one place; not based on a business rule you specified.
- **Daily rules default shift length**: 12 hours (`DEFAULT_SHIFT_HOURS` in `src/dispatcher/rules/daily.py`), extendable via `DailyRules.extend()`. You didn't specify an exact shift length, so this is a placeholder.
- **Message-type classification heuristics** in `storage.classify_incoming()` (e.g. "contains 'closed' + a dollar amount" → `CLOSING`, "contains 'cancel'" → `CANCELLATION`) are keyword-based, not a business rule you dictated. Anything that doesn't match a known pattern comes back `UNRECOGNIZED` rather than being force-fit into the wrong category.

None of these block using the system today — they just mean many real jobs will currently land on `HANDLE_MANUALLY` (mainly due to unconfirmed dispatch/closing groups) until the underlying business data is filled in.
