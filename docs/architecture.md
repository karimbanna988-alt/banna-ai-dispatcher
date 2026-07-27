# Phase 1 Architecture — Complete Execution Flow

**Status: Phase 1 core system is built and tested (126 pytest cases passing, including app.py integration tests -- see section 5).**
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
| `rules/service_areas.yaml` | Permanent service-area data (13 known areas incl. Raleigh NC; dispatch/closing groups intentionally left unconfirmed; recurring weekly days off for Chicago/Raleigh) | — | — | **DONE (placeholder data)** |
| `src/dispatcher/rules/service_areas.py` | Look up city/state in the permanent service-area mapping; `is_recurring_day_off()` helper | city, state, area list | `ServiceAreaLookup` (confirmed/unconfirmed/inactive/not_found) | **DONE** |
| `src/dispatcher/rules/hours.py` | Permanent business-hours check (8am-10pm America/New_York, every day, IANA tz-aware) | optional tz-aware datetime | `HoursCheck` | **DONE** |
| `src/dispatcher/rules/daily.py` | Parse the dispatcher's freeform message into optional, random daily exceptions (technician off/busy, one-off area unavailability, temporary restrictions) | freeform text | `DailyRules` | **DONE** |
| `src/dispatcher/rules/__init__.py` | Accept/pass/needs-review/handle-manually decision: parser override → business hours → area lookup → recurring day off → daily exceptions → vehicle/service restrictions → confidence | `ExtractedJob`, `CompanyProfile`, service areas, `DailyRules` | `Decision` | **DONE** |
| `src/dispatcher/rules/__init__.py::force_handle_manually` | Escalate an already-computed `Decision` to `HANDLE_MANUALLY` for cross-cutting overrides decided outside `decide()`'s own checks | `Decision`, reason | mutated `Decision` | **DONE** |
| `src/dispatcher/formatter.py` | Convert `ExtractedJob` into the Luxor Locksmith-branded technician message (V1 rule) | `ExtractedJob` | technician message (string) | **DONE** |
| `src/dispatcher/updates.py` | Map a technician's confirmed status text to the standard short company reply | freeform tech status text | `StandardReply` | **DONE** |
| `src/dispatcher/storage.py` | SQLite job records; classify incoming messages against existing records; append-only audit log | `ExtractedJob` + every stage's output + human actions | persisted rows | **DONE** |
| `src/dispatcher/pipeline.py` | The single `extract_job → classify_incoming → get_company_profile → decide → format_technician_message` sequence, shared by `app.py`'s Process tab and the fake-inbox simulator | raw text, vehicle rules, service areas, conn, daily rules | `ProcessResult` | **DONE** |
| `src/dispatcher/simulator.py` | Fake Quo inbox: scripted, ordered conversation scenarios delivered into the same session_state the real UI uses -- no real Quo message is ever sent | scenario selection + step index | populates the Process/Status/Closing tabs as if a real message arrived | **DONE** |
| `simulator/scenarios/**` | Scenario fixture data (fake, `SIM*` ticket prefixes) — one folder per scenario, ordered step files | — | — | **DONE** |
| `app.py` | Streamlit UI; orchestrates every module above; Approve/Edit/Reject/Handle Manually + Daily Rules/Status/Closing/Audit/Fake-Inbox tabs; shows live business-hours status | dispatcher input | approval screen + writes to storage | **DONE** |
| `tests/test_*.py` | Unit coverage of every `src/dispatcher` module in isolation | — | — | **DONE** |
| `tests/test_app_integration.py` | Integration coverage of `app.py` itself via `streamlit.testing.v1.AppTest`, driven through the fake-inbox simulator -- one test per conversation scenario, asserting on the resulting audit log | — | — | **DONE** (126 tests passing overall) |

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
    CLOSING_WITHOUT_DISPATCH = "CLOSING_WITHOUT_DISPATCH"
    UNRECOGNIZED = "UNRECOGNIZED"

@dataclass
class ClassifyResult:
    message_type: MessageType
    existing_job_id: Optional[int]
    conflicting_fields: List[str] = field(default_factory=list)
    reason: str = ""
```

**Hard-override chain, in the order `rules.decide()` actually checks them** (rewritten 2026-07-27 per the confirmed business-hours/recurring-schedule model):
1. `job.status == HANDLE_MANUALLY` (set by `parser.py`: missing/conflicting required field, unrecognized source, unclear vehicle, or low overall confidence) → `Verdict.HANDLE_MANUALLY`, always, regardless of anything else.
2. **Business hours**: outside 8am-10pm America/New_York → `HANDLE_MANUALLY`, reason "Outside operating hours" (blanket check, before anything area-specific; the business operates every day, so this doesn't vary by weekday).
3. Service area not found in the mapping → `HANDLE_MANUALLY`.
4. Service area found but its dispatch/closing group is unconfirmed → `HANDLE_MANUALLY` (never guesses a group name — this is the current state of every real entry in `rules/service_areas.yaml` today).
5. Service area marked inactive → `PASS`.
6. **Recurring weekly day off** for this area (e.g. Chicago on a Saturday) → `PASS` or `HANDLE_MANUALLY` per that area's configured `recurring_day_off_action` — unless a daily exception explicitly marks the area "open" today, which overrides it.
7. Daily exceptions (fully optional -- no daily input at all is fine and does **not** force `HANDLE_MANUALLY` by itself, unlike the earlier design): area marked temporarily unavailable today, or the effective technician status is "busy"/"off" → `PASS`. Effective technician status "unknown" (no daily override and the area's own `default_technician_availability` is "unknown") → `HANDLE_MANUALLY`. A matching daily temporary restriction → `PASS`.
8. Vehicle restricted → `PASS`; vehicle restriction status `"unclear"` (safety net; parser should already have caught this) → `HANDLE_MANUALLY`.
9. Otherwise: `ACCEPT` if `job.overall_confidence >= 80`, else `NEEDS_REVIEW` (job data was clean and extractable, but confidence is borderline — double-check before approving).

## 4. Placeholders and assumptions made to keep moving (per your instruction not to pause)

These are safe, editable, and clearly marked as TODO — none of them silently guess a business fact:

- **`rules/service_areas.yaml`**: 13 named areas are in the file (the original 12 plus Raleigh, NC), but every `dispatch_group`/`closing_group` is `null` ("unconfirmed"). This means **every job today, in every one of those areas, currently resolves to `HANDLE_MANUALLY`** until you fill in real group names — see the file's own TODO comments. Several city names (Arlington, Augusta, Columbia, Greenville, Chicago, Vancouver) are ambiguous across multiple US states, so `state` was left blank for those rather than guessed; only "Charlotte, NC" and "Raleigh, NC" had an explicit state.
- **`default_technician_availability: available`** on every area (confirmed 2026-07-27): an explicit, temporary testing default per your instruction, not a claim any specific technician is actually confirmed. Will be replaced by a real technician-availability workflow later.
- **Recurring weekly days off**: only Chicago (Saturday) and Raleigh, NC (Sunday) are confirmed. Every other area has `recurring_day_off: null` (no known recurring closure) until you provide the rest of the schedule. `recurring_day_off_action` defaults to `PASS` for both confirmed entries — change it per-area if you'd rather it force `HANDLE_MANUALLY` instead.
- **AGN's closing format**: **confirmed 2026-07-27** against a real AGN closing example (ticket UQ9DI, `$690 Cash`) — same repeat-fields + "Closed" + amount + payment-method shape as Lucky's, just a different payment method ("Cash" vs "CC") and no parts, both already handled generically by `ClosingInput`. No template change was needed; this validates the shared-template design. See `tests/test_closing.py::test_agn_closing_against_real_confirmed_shape`.
- **AGN name field**: a related real bug was caught from that same example — AGN sometimes provides a customer name via a bare `N:` label, which the parser didn't look for at all (it had hardcoded "AGN/Lucky never have a name" from a single earlier sample). Fixed in `parser.py`: `NAME_RE` now matches `Name:` or `N:`, and name extraction is attempted for every source rather than skipped for non-Main-Leads.
- **Luxor Locksmith technician message template**: implements your Version 1 rule exactly (strip source branding, preserve every field verbatim, clean spacing only). It has not been visually reviewed by you yet — treat the current field order/labels in `src/dispatcher/formatter.py` as a first draft you can adjust.
- **`NEEDS_REVIEW_CONFIDENCE_THRESHOLD = 80`** in `src/dispatcher/rules/__init__.py`: an engineering placeholder distinguishing a clean `ACCEPT`/`PASS` from one that's technically decidable but worth a second look. Adjustable in one place; not based on a business rule you specified.
- **Daily rules default shift length**: 12 hours (`DEFAULT_SHIFT_HOURS` in `src/dispatcher/rules/daily.py`), extendable via `DailyRules.extend()`. You didn't specify an exact shift length, so this is a placeholder.
- **Message-type classification heuristics** in `storage.classify_incoming()` (e.g. "contains 'closed' + a dollar amount" → `CLOSING`, "contains 'cancel'" → `CANCELLATION`) are keyword-based, not a business rule you dictated. Anything that doesn't match a known pattern comes back `UNRECOGNIZED` rather than being force-fit into the wrong category.

None of these block using the system today — they just mean many real jobs will currently land on `HANDLE_MANUALLY` (mainly due to unconfirmed dispatch/closing groups) until the underlying business data is filled in.

## 5. Fake Quo inbox simulator (2026-07-27, local only -- no real Quo integration)

Manual testing (pasting real, anonymized jobs one at a time) validated the
parser/decision/formatter path well, but only ever exercised a *single new
message* -- never a full conversation (reminder, correction, cancellation,
status update, closing), and never `app.py` itself (every test before this
covered `src/dispatcher/*` in isolation).

- **`src/dispatcher/pipeline.py`** extracts the `extract_job → classify_incoming
  → get_company_profile → decide → format_technician_message` sequence that
  used to be inlined in `app.py`'s Process-tab button handler. Both the real
  "paste a message" flow and the simulator's "deliver a scripted message"
  flow call this one function -- there is exactly one place this
  orchestration exists.
- **`src/dispatcher/simulator.py`** + **`simulator/scenarios/**`** (fake
  `SIM*`-prefixed ticket data) implement a **"Fake Inbox (Simulator)"** tab in
  `app.py`. A scenario is an ordered list of steps (`message` / `status_phrase`
  / `closing_input`); "Deliver next message" pushes the next step into the
  same `st.session_state` keys a real paste+click would populate, so the
  existing review screen and Approve/Edit/Reject/Handle Manually buttons work
  completely unchanged. **Nothing is ever sent to a real Quo** -- this only
  exercises the local audit log.
- Delivery is **manual-advance, not timed**: a human is meant to actively
  review/act on each delivered step, so "arrives automatically" means *the
  tester never retypes or copy-pastes a message*, not unattended timing.
- Scenarios cover all 6 requested conversation types (new job x3 sources,
  reminder/duplicate, corrected info, cancellation, technician status update,
  closing) **plus two scenarios that deliberately document known
  classification gaps** rather than hide them -- see section 6.
- **`tests/test_app_integration.py`** drives `app.py` itself headlessly via
  `streamlit.testing.v1.AppTest`, one test per scenario, asserting on the
  resulting `storage.get_audit_timeline()` contents. Each test isolates its
  own SQLite file (`monkeypatch.setattr(storage, "DB_PATH", ...)` +
  `st.cache_resource.clear()`) so nothing ever touches the real
  `data/dispatcher.db` or leaks a cached connection between tests.
- **Status Update tab** previously had no persistence at all -- confirming a
  technician's status never wrote anything to the audit log. Now wired the
  same way the Closing tab already does it (build a `ClassifyResult`
  manually, call `storage.save_audit_entry`), so status updates are now part
  of a ticket's permanent history.

Next phase (not started): real Quo API integration, read-only, via
incoming-message webhooks -- only after the criteria in section 7 are met.

## 6. Known limitations (found, documented, explicitly accepted for V1)

- **Vehicle-make list completeness**: `parser.py`'s `MAKES` list is not
  exhaustive (e.g. Isuzu is not recognized). Nothing is lost when this
  happens -- the mention still shows up verbatim in the notes field -- it's
  just not pulled into the structured `vehicle` field.
- **Reminder detection is exact-match only**: `storage.classify_incoming`
  only classifies `DUPLICATE` for a byte-identical repeat. A reminder for the
  same ticket, reworded even slightly, falls through to `UNRECOGNIZED`. See
  `tests/test_app_integration.py::test_reworded_reminder_is_a_documented_gap_not_duplicate`.
- **Cancellation detection is a literal substring check** for the word
  "cancel". Several real messages hit during manual testing 2026-07-27 ("no
  longer needs service", "customer found her keys...", "will call back later
  hung up") never say "cancel" and fall through to `UNRECOGNIZED` today. See
  `tests/test_app_integration.py::test_cancellation_worded_differently_is_a_documented_gap`.
- **Intra-message data inconsistency isn't cross-validated**: e.g. a
  structured `Address:` field's zip code differing from a zip mentioned in
  the freeform notes of the *same* message isn't flagged -- both are
  preserved verbatim, but nothing compares them to each other.

None of these cause information loss, an incorrect decision, or incorrect
routing on their own -- that's exactly why they're accepted limitations
rather than blocking bugs. Fixing any of them is a deliberate future choice,
not an oversight.

## 7. Parser V1 stability: acceptance criteria

Defined 2026-07-27 so real-job testing has a bounded exit point instead of
continuing indefinitely. **Not all met yet** -- this section records the bar,
not a claim it's been cleared.

1. **Unit tests**: full `pytest` suite green (126 tests today, `tests/test_*.py`).
2. **Integration tests**: every scenario in `tests/test_app_integration.py`
   green, including the two documented-gap scenarios passing with their
   *documented* (not necessarily ideal) expected behavior.
3. **Manual real-job testing**: the last 3 consecutive batches of 5 real jobs
   each (15 jobs) processed with zero bugs in the categories that matter --
   incorrect extraction, incorrect decision, incorrect routing, or
   information loss. Cosmetic issues don't reset this counter.
4. **This Known Limitations section (§6)** stays current -- anything newly
   found-but-accepted gets added here, not left undocumented.
5. **Sign-off checklist**: Extraction / Decision engine / Formatter / Audit
   logging each explicitly confirmed via both unit and integration tests.
6. Once 1-5 are met, this section gets a **"Parser V1 -- STABLE"** marker with
   the date it was earned; any later behavior change to parser/decision/
   formatter requires a new dated note here, not silent drift.
