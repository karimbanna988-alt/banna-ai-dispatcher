# Phase 1 Architecture — Complete Execution Flow

**Status: Phase 1 core system is built and tested (147 pytest cases passing, including app.py integration tests -- see sections 5 and 8).**
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
| `src/dispatcher/quo_client.py` | Read-only Quo (OpenPhone) API client -- GET only, structurally incapable of sending/modifying anything | HTTP calls to `api.quo.com/v1` | `Conversation`/`QuoMessage`/`Contact` objects | **DONE** |
| `rules/quo_dispatch_groups.yaml` | The four confirmed dispatch-group Quo Contact ids (Lucky/CLOSINGS/AGN/Main Leads 2) + our Quo phone number id -- business config, not code | — | — | **DONE (confirmed 2026-07-27)** |
| `src/dispatcher/quo_ingest.py` | Direct polling of the configured dispatch-group contacts (not conversation discovery) + fetch-time dedup; renders the "Quo Inbox (Live)" tab | `QuoClient`, `conn` | queued raw messages fed into `pipeline.process_raw_message` | **DONE** |
| `scripts/quo_probe_readonly.py` | One-off manual script (outside `tests/`, not pytest-collected): real read-only GET calls to confirm envelope/direction/text-format assumptions | your real `QUO_API_KEY` | printed JSON | **DONE (run and confirmed 2026-07-27)** |
| `app.py` | Streamlit UI; orchestrates every module above; Approve/Edit/Reject/Handle Manually + Daily Rules/Status/Closing/Audit/Fake-Inbox/Quo-Live tabs; shows live business-hours status | dispatcher input | approval screen + writes to storage | **DONE** |
| `tests/test_*.py` | Unit coverage of every `src/dispatcher` module in isolation | — | — | **DONE** |
| `tests/test_app_integration.py` | Integration coverage of `app.py` itself via `streamlit.testing.v1.AppTest`, driven through the fake-inbox simulator and the live Quo tab (mocked client) -- one test per scenario, asserting on the resulting audit log | — | — | **DONE** (147 tests passing overall) |

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

Next phase: real Quo API integration, receive-only -- see section 8. Started
2026-07-27, ahead of the section 7 criteria being fully met (that gate is
for *behavior* stability; wiring up a real, structurally read-only message
source doesn't change parser/decision/formatter behavior at all).

## 6. Known limitations (found, documented, explicitly accepted for V1)

- **Vehicle-make list completeness**: `parser.py`'s `MAKES` list is not
  exhaustive (e.g. Isuzu is not recognized). Nothing is lost when this
  happens -- the mention still shows up verbatim in the notes field -- it's
  just not pulled into the structured `vehicle` field.
- **Reminder detection is exact-match only**: `storage.classify_incoming`
  only classifies `DUPLICATE` for a byte-identical repeat. A reminder for the
  same ticket, reworded even slightly, falls through to `UNRECOGNIZED`. See
  `tests/test_app_integration.py::test_reworded_reminder_is_a_documented_gap_not_duplicate`.
- **Intra-message data inconsistency isn't cross-validated**: e.g. a
  structured `Address:` field's zip code differing from a zip mentioned in
  the freeform notes of the *same* message isn't flagged -- both are
  preserved verbatim, but nothing compares them to each other.

None of these cause information loss, an incorrect decision, or incorrect
routing on their own -- that's exactly why they're accepted limitations
rather than blocking bugs. Fixing any of them is a deliberate future choice,
not an oversight.

**Fixed 2026-07-29**: cancellation detection was previously a literal
substring check for the word "cancel" only; several real messages hit
during manual testing 2026-07-27 ("no longer needs service", "customer
found her keys...", "will call back later hung up") never said "cancel"
and fell through to `UNRECOGNIZED`. `storage._is_confident_cancellation()`
now also recognizes a small, explicit set of conservative, deterministic
reworded phrasings (still no LLM, no probabilistic classification), with
a narrow hedge guard so a hypothetical or interrogative mention of
cancellation ("customer may cancel", "asked whether cancellation is
possible") is deliberately *not* treated as a confident cancellation and
still falls through to `UNRECOGNIZED` for manual review. See
`tests/test_storage.py` (`test_reworded_cancellation_*`) and
`tests/test_app_integration.py::test_cancellation_worded_differently_is_now_correctly_classified`.

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

## 8. Real Quo API integration (2026-07-27, receive-only)

Quo is the rebranded OpenPhone SMS platform (`api.quo.com`). This phase
connects to the real API in **receive-only mode, structurally, not by
convention**: `src/dispatcher/quo_client.py` contains no function capable
of POST/PUT/PATCH/DELETE to Quo -- only GET. It is not possible for this
code to send a reply, register a webhook, or otherwise modify the Quo
account, because that capability does not exist in the module. A test
(`test_module_source_contains_no_write_http_verbs` in
`tests/test_quo_client.py`) enforces this by grepping the module's own
source and asserting none of those verbs appear.

- **`src/dispatcher/quo_client.py`**: `QuoClient` wraps `GET /v1/conversations`
  and `GET /v1/messages` with auto-pagination (`nextPageToken`), a
  client-side throttle (Quo's documented limit: 10 req/sec/key), and 429
  retry-with-backoff. Auth: `Authorization: <raw key>` header (confirmed
  from Quo's docs -- **not** a Bearer token). API key comes only from the
  `QUO_API_KEY` env var (see `.env.example`), never hardcoded.
- **Polling, not webhooks, this phase**: Quo does support webhooks
  (`POST /v1/webhooks/messages`), but registering one is itself a write to
  the Quo account, and receiving one requires this locally-run app to be
  publicly reachable, which it isn't.
- **Direct polling of known dispatch-group contacts, not conversation
  discovery** (redesigned 2026-07-27 after real-world testing): the
  original design called `GET /v1/conversations` with no filters to
  discover new/unknown senders generically. Testing against the real
  account found this endpoint's "most recent" ordering lags real message
  activity by over an hour -- new incoming messages from the four known
  dispatch groups (Lucky, CLOSINGS, AGN, Main Leads 2) did not appear in
  the conversations list even minutes after arriving, though they were
  fetchable directly via `GET /v1/messages` the whole time. The business
  owner confirmed these four specific Quo contacts are the only sources
  that matter, so polling now targets them directly instead:
  `rules/quo_dispatch_groups.yaml` lists each group's stable Quo Contact
  id (**not** a phone number -- resolved fresh via `GET /v1/contacts/{id}`
  on every poll, so a renumbered contact never needs a code change) plus
  `our_phone_number_id` (confirmed 2026-07-27: only one Quo number exists
  on this account). Also confirmed against real messages: a conversation's
  displayed name in the Quo UI ("Lucky 🍀" etc.) is **not** a field on the
  conversation object (`name` was `null` on all 1,000 most recent
  conversations checked) -- it's a saved Contact label
  (`defaultFields.firstName`/`lastName`/`company`), which is why contacts,
  not conversations, are the right thing to poll by.
- **`src/dispatcher/quo_ingest.py`**: `poll_for_new_messages(conn, client,
  config=None)` -- loads `rules/quo_dispatch_groups.yaml` (or an injected
  `QuoPollConfig` for tests), resolves each configured contact's current
  phone number, and polls `GET /v1/messages` directly for each. Checkpoint-
  based (`quo_poll_state` table; first-ever poll uses a bounded lookback
  window, `QUO_POLL_INITIAL_LOOKBACK_MINUTES`, default 15 min, rather than
  importing the org's entire history), filters to inbound messages only
  (`direction == "incoming"`, confirmed against real messages -- the other
  observed value is `"outgoing"`), and dedupes against a
  `quo_processed_messages` table **written at fetch time** -- so an
  overlapping poll window or an app restart is structurally unable to
  re-queue the same message, regardless of what the dispatcher later does
  with it. Raw message text is handed to `pipeline.process_raw_message()`
  **unmodified**. Each queued message carries its source group's label
  (e.g. "AGN") so the dispatcher can see which company it came from before
  loading it into review.
- **Confirmed against real messages 2026-07-27**: Main Leads 2 and AGN
  send the exact structured formats the parser already expects
  (`Power By Ucallz App`/`Ticket:` and `Ref: AGN Locksmith Services`/`PDL:`
  respectively) as the literal SMS body -- no transformation needed between
  Quo and the parser. CLOSINGS is different by design, not a bug: it's a
  back-and-forth thread where Lucky sends short operational replies and
  **we** send the actual structured closing report as an *outgoing*
  message (generated by the existing Closing tab) -- the inbound filter
  correctly skips our own outgoing closing text there.
- **UI**: a 7th, appended "Quo Inbox (Live)" tab in `app.py`
  (`quo_ingest.render_quo_live_tab`). "Poll Quo now" is a manual button, not
  a timer -- same reasoning as the simulator's manual-advance design: a
  human has to review/act on each message regardless, so a background timer
  adds real complexity for no workflow benefit. "Load next Quo message into
  review" reuses the fake-inbox simulator's exact delivery mechanism
  unmodified (`st.session_state.sim_pending_delivery`,
  `simulator.apply_pending_delivery`) -- it has no idea whether the text
  came from a scenario fixture or a real Quo message, so no new delivery
  code path was needed, only a new producer of the same payload shape. From
  there: identical Approve/Edit/Reject/Handle Manually -> `_finalize()` ->
  `storage.save_audit_entry` as every other message source.
- **Confirmed against real API responses 2026-07-27** (`scripts/quo_probe_readonly.py`
  plus a manual scan of the four dispatch groups): the JSON envelope is
  `{"data": [...], "nextPageToken": ..., "totalItems": ...}` exactly as
  coded (`quo_client._unwrap_list()`), and `direction` is literally
  `"incoming"`/`"outgoing"` (`quo_ingest.INBOUND_DIRECTION_VALUES`) --
  both matched the original documentation-only assumptions, no code
  changes were needed for either.
- **Tests**: `tests/test_quo_client.py` and `tests/test_quo_ingest.py` --
  zero real network calls, everything mocked/stubbed (one test does load
  the real `rules/quo_dispatch_groups.yaml` to confirm it parses and lists
  the four expected groups, but never makes a network call with it).
  Extended `tests/test_app_integration.py` with the same AppTest-driven
  pattern used for every simulator scenario, using a monkeypatched fake
  `QuoClient`.
- **Confirmed working end-to-end 2026-07-27/28**: this line previously
  said a real message hadn't been run through "Poll Quo now" and approved
  yet -- that's since happened multiple times against the real account
  (real AGN and Main Leads jobs, correctly extracted/classified/formatted/
  audited, tagged `channel=shadow_mode_quo_live`, zero Quo writes). See
  section 9 for what was built on top of this since then.

## 9. Phase 1 CRM foundation (2026-07-28)

Three additive, independently-committed changes layered on top of the
receive-only Quo integration (section 8) and the crash-fix/shadow-mode
work: a durable review queue, a database schema repair, and a job-status
state machine plus a "Jobs" list view. None of this touches the parser,
decision engine, formatter, or the `events` table's role as the one
append-only audit source of truth -- this is still a **local, human-in-
the-loop prototype**: nothing sends, replies to, acknowledges, or
modifies anything in Quo, no action happens without a dispatcher clicking
Approve/Edit/Reject/Handle Manually, and none of this is autonomous or
production-ready.

### 9a. Durable Quo pending queue (fixes silent message loss on restart)

Before this change, a message polled from Quo lived only in
`st.session_state.quo_pending_queue` (Streamlit session memory) once
fetched, while its Quo message id was already recorded in
`quo_processed_messages` at fetch time. If the app or laptop restarted
before the dispatcher acted on a polled message, its content was gone --
but its id was already marked processed, so it would never be re-fetched
either. Silent, permanent, unrecoverable loss of that one message's
content.

**Fix**: a new `quo_pending_messages` table (`src/dispatcher/storage.py`)
is now the sole source of truth for the review queue -- the "Quo Inbox
(Live)" tab reads it fresh from the database on every render, not from
session state. Columns: `message_id` (Quo's own id, primary key),
`conversation_id`, `group_label`, `raw_text`, `from_number`,
`received_at`, `fetched_at`, `status`, `loaded_at`, `actioned_at`,
`last_error` (reserved for future use -- see the honest limitation note
below).

**Queue state transitions**:

```
pending --[Load next Quo message into review]--> loaded --[Approve/Edit/Reject/
                                                             Handle Manually
                                                             succeeds]--> actioned
   ^                                                  |
   +---- a restart-interrupted review (row left       |
         "loaded", never actioned) is picked up       |
         again by the *next* "Load next" click,       |
         not stranded ---------------------------------+
```

No row is ever deleted in this phase (an append-only ledger, matching the
`events` table's own philosophy).

**Atomic persistence** (`storage.save_pending_and_mark_processed`): the
full message content (`quo_pending_messages`) and the id-level dedup
marker (`quo_processed_messages`) are written in **one transaction** -- a
message id is never marked processed unless its content was already
safely stored. Verified empirically (not assumed) that Python's stdlib
`sqlite3`, with an explicit `BEGIN`, treats a mix of `INSERT`s as fully
rollback-able together. Any failure calls `conn.rollback()` and
re-raises.

**Checkpoint failure behavior**: `quo_ingest.poll_for_new_messages` now
returns `PollOutcome(queued, failed_count)`. If **any** message in a poll
fails to persist, the poll's checkpoint update is skipped entirely for
that whole poll cycle (the existing design has one global checkpoint
across all four dispatch groups, not a per-contact one -- see the honest
limitation note below for why). On the next poll, the same time window is
re-scanned: messages that already succeeded are skipped again via the
untouched `quo_processed_messages` ledger (their ids are already marked,
regardless of this poll's checkpoint decision), while the message that
failed -- never marked processed -- is naturally retried. The "Quo Inbox
(Live)" tab surfaces a failure count (e.g. "2 message(s) could not be
saved durably...") without ever displaying the failed message's content.

**Honest limitations, not hidden**:
- `last_error` is in the schema per the original design brief but isn't
  populated by the current write path -- a failed *initial* persist is
  fully rolled back (both inserts undone together), so there is no row
  left to attach an error string to. It exists for forward compatibility,
  not decoration.
- The checkpoint is global across all four configured dispatch groups
  (unchanged from section 8's design), not per-contact -- "hold the
  checkpoint back on any failure" was implemented at the whole-poll level
  rather than introducing per-contact checkpoint columns, which would
  have been a larger, unrequested schema change. The dedup ledger still
  guarantees no message is ever lost or double-queued regardless.

Tests: `tests/test_quo_ingest.py` (durable persistence, restart survival,
a simulated database-write failure leaving the checkpoint untouched and
the message unprocessed, retry-without-duplication on the next poll,
pending/loaded/actioned transitions including restart-interrupted-review
recovery) and `tests/test_app_integration.py` (the same, driven through
the real `app.py` UI via `AppTest`, including a fresh `AppTest` instance
against the same database file to simulate a restart).

### 9b. `events.job_id` foreign-key schema repair

**Root cause**: the earlier jobs-table migration (section on the
UNIQUE-constraint crash fix) renamed `jobs` away and back
(`ALTER TABLE jobs RENAME TO jobs_pre_source_scoped_unique_backup`, then
`CREATE TABLE jobs ...`). SQLite's `ALTER TABLE ... RENAME` automatically
rewrites `FOREIGN KEY` clauses in *other* tables to keep following the
renamed table -- so `events.job_id`'s persisted schema ended up
referencing `jobs_pre_source_scoped_unique_backup` instead of `jobs`.
`PRAGMA foreign_keys` defaults OFF per connection in SQLite, so this was
never enforced, but it was still wrong, and `PRAGMA foreign_key_check`
against the live database confirmed a real violation once you checked
against the *correct* table.

**Repair** (`storage._repair_events_job_id_foreign_key`): deliberately
does **not** repeat the rename-old-table-first sequence that caused the
original bug. Follows SQLite's own recommended generalized pattern for
schema changes `ALTER TABLE` can't express directly: build the corrected
table fresh under a temporary name, copy every row across with `id`s
preserved, verify row counts match and there are zero orphan `job_id`
values, only then drop the already-copied original and rename the
temporary table into place -- that rename targets a table nothing else
references yet, so it cannot trigger the same class of bug. Before any of
that, it also refuses to proceed automatically if any view, trigger, or
other table's foreign key still references `"events"` by name. The whole
operation is one transaction, gated by `PRAGMA foreign_key_check` and
`PRAGMA integrity_check` before commit; any failure rolls back
everything, leaving the original table completely untouched.

**Backup method**: a timestamped file-level copy of `data/dispatcher.db`
was taken (kept outside Git, in a sibling directory to the repo) before
running this migration against the real database -- deliberately
*instead of* adding another live backup table, since the file-level
backup already provides full recoverability without cluttering the live
schema. The pre-existing `jobs_pre_source_scoped_unique_backup` table
from the earlier migration is left in place, untouched (not deleted).

**Enforcement**: `storage.connect()` now explicitly sets
`PRAGMA foreign_keys = ON` on every connection -- but only after
verifying `events.job_id` is currently clean against `jobs.id` (a cheap
indexed check, since the pragma is a per-connection setting, never
persisted in the database file, and SQLite defaults it OFF). Self-healing
rather than blind: if orphans were ever found, enforcement simply isn't
turned on for that connection rather than crashing.

Applied to the real `data/dispatcher.db`: all 6 event rows preserved with
their original ids, zero `foreign_key_check` violations, `integrity_check`
`"ok"`, enforcement now on.

Tests: `tests/test_storage.py` (migration correctness against a hand-built
pre-repair schema, idempotency, no extra live backup table created,
existing `jobs` backup left alone, `foreign_key_check`/`integrity_check`
pass, enforcement rejects an invalid `job_id`, and two safety-guard tests
-- refuses to migrate automatically when a view references `events`, and
aborts/reports orphan rows without enabling enforcement if any are found).

### 9c. Job status state machine

`jobs.status` is derived by **one** deterministic function,
`storage.derive_job_status(previous_status, message_type, final_action)`,
used identically by the live `save_audit_entry()` save path, the one-time
backfill, and every test -- never inferred ad hoc from only a job's
latest event.

| `final_action` | `message_type` | -> `status` |
|---|---|---|
| Handle Manually | *(any)* | **Handle Manually** (always wins) |
| Approve / Edit | NEW | **Approved** |
| Reject | NEW | **Rejected** (the *only* path to Rejected) |
| Approve / Edit | STATUS | **In Progress** |
| Approve / Edit | CLOSING | **Closed** |
| Approve / Edit | CANCELLATION | **Cancelled** |
| Reject | STATUS / CLOSING / CANCELLATION | preserves the job's current status |
| Approve / Edit / Reject | CORRECTION / UPDATE_REQUEST / DUPLICATE / UNRECOGNIZED | preserves the job's current status |
| *(any)* | CLOSING_WITHOUT_DISPATCH | **Needs Review** (anomalous by construction) |
| unknown action, unknown message type, or no valid previous status to preserve | -- | **Needs Review** (safe fallback) |

The eight status values are fixed and deterministic: New, Needs Review,
Approved, Handle Manually, Rejected, In Progress, Closed, Cancelled.
"New" is the seed value for a job's first-ever event (and the fallback a
malformed/out-of-order history preserves if its very first recorded event
is something other than NEW) -- in normal operation every real job leaves
"New" on its first save.

`jobs.status`, `jobs.last_event_id`, `jobs.last_event_at` are set
together on every `save_audit_entry()` call, and backfilled for existing
jobs by **replaying each job's events chronologically** through
`derive_job_status` (not by reading only the latest event -- proven by a
test where only full replay can produce the correct answer, since several
transitions depend on knowing the status *before* the current event).
Backfill is idempotent and resumable: `last_event_id IS NULL` is the "not
yet backfilled" marker, so an interrupted backfill safely continues on
the next `connect()` rather than silently stopping partway. Applied to
the real database: all 5 jobs' backfilled statuses match their event
history exactly.

Tests: `tests/test_job_status.py` -- the full transition table above
including every fallback/ambiguous case, live-save status progression
through a realistic NEW -> STATUS -> CLOSING sequence, confirmation that
rejecting a later status report preserves an already-approved job's
status, chronological-replay-vs-latest-event-only backfill correctness,
and backfill idempotency/resumability.

### 9d. "Jobs" tab

Appended as an 8th tab (existing tab indices for Process/Daily/Status/
Closing/Audit/Simulator/Quo-Live are unchanged). One row per
`(source, ticket_number)` via `storage.list_jobs()` (parameterized
filters: ticket search, source, status, "active since" date). The list
view selects only `source`/`ticket_number`/`status`/timestamps/
`latest_action` -- it never selects `raw_message` or `latest_fields_json`
at all, so phone numbers, addresses, and full message text cannot leak
into the list by construction; no separate masking step was needed.
Selecting a job shows its full, unmasked chronological timeline via
`storage.get_audit_timeline_by_job_id()` (parameterized, keyed by
`job_id` rather than `ticket_number` so it can't cross-source-collide),
rendered through the same `_render_event_timeline()` helper the existing
Audit Timeline tab uses -- the `events` table remains the one audit
source of truth for both.

Not built in this phase (explicitly out of scope): a `customers` table,
analytics/revenue reporting, structured closing-amount storage, data
export, or any change to Quo's write capability.

### 9e. Quo access is still strictly GET-only

None of sections 9a-9d add, modify, or enable any HTTP method other than
GET against Quo. `src/dispatcher/quo_client.py` and
`src/dispatcher/quo_ingest.py` were re-verified (structural grep guard
test, `tests/test_quo_client.py::test_module_source_contains_no_write_http_verbs`)
to contain zero `.post(`, `.put(`, `.delete(`, or `.patch(` calls after
every change in this phase. Nothing here sends, replies to, acknowledges,
or modifies anything in the real Quo account.

## 10. Technician assignment (2026-07-28, corrected same day)

**Before this**: no technician-identity concept existed anywhere in this
codebase or in any `rules/*.yaml`/`.json` config file -- confirmed by
direct inspection of `storage.py`, `rules/__init__.py`,
`rules/service_areas.py`, and `rules/daily.py`. The only prior notion was
*area-level* `default_technician_availability` (available/busy/off/
unknown) in `rules/service_areas.yaml`, plus a freeform per-shift
override in `rules/daily.py` (e.g. "Charlotte technician busy") -- neither
names an individual technician. A `technicians` table was therefore
genuinely required; nothing existing could safely be reused.

**Corrected same day, before any real data was entered**: the first pass
put `dispatch_group`/`closing_group` on the *service area*, gated by
whether both were set ("confirmed" vs "unconfirmed"). Real WhatsApp
screenshots showed this was wrong: one area (e.g. Greenville) can have
several technician-specific dispatch/closing groups, which a single
area-level string can't represent without arbitrarily picking one and
silently misrouting the rest. The business owner's correction: **service
areas hold only geographic/business facts; dispatch/closing group names
are opaque, technician-owned destination strings the AI never parses or
reasons about.** Everything below reflects the corrected model -- the
brief unconfirmed/area-level-group design was replaced before it was ever
used with real data.

### 10a. Schema (additive, idempotent -- same pattern as every prior migration)

- `rules/service_areas.yaml` / `ServiceAreaRecord`: **no** `dispatch_group`
  or `closing_group` fields -- geographic/business facts only (city/state,
  `active`, recurring day off, default technician availability, vehicle/
  company restrictions).
- New `technicians` table: `name` (unique), `active` flag, `areas_json`/
  `skills_json`/`vehicle_restrictions_json` (JSON string lists of service-
  area names, not group names), plain `dispatch_group`/`closing_group` TEXT
  columns (nullable, opaque, e.g. a WhatsApp group name), timestamps.
- New `jobs.assigned_technician_id` column (nullable, `REFERENCES
  technicians (id)`) -- mirrors the job's *current* assignment, exactly
  like `jobs.status`/`last_event_id` already mirror current lifecycle
  state while `events` remains the one append-only history. Never an
  alternate source of truth: every change to this column happens only
  alongside a new `events` row recording the same change.
- `storage._configure_foreign_key_enforcement()` extended to also verify
  `assigned_technician_id` has zero orphans (alongside the pre-existing
  `events.job_id` check) before turning `PRAGMA foreign_keys = ON`.

### 10b. Roster

`add_technician`, `set_technician_active`, `list_technicians`,
`get_technician` -- plain CRUD, no eligibility logic. `areas` must be
**service-area names** (the same geographic identity `rules/service_areas.yaml`
and `rules.decide()` already resolve to, e.g. "Charlotte") -- never a
WhatsApp group name, so eligibility checks stay anchored to the one
existing area concept. `skills`/`vehicle_restrictions` are optional and
explicit: empty means "no additional restriction beyond the global
vehicle-restriction rules `rules.decide()` already enforces before a job
ever reaches assignment" -- never inferred. `dispatch_group`/
`closing_group` are opaque strings, purely informational -- stored and
later displayed verbatim, never matched against or reasoned about.

### 10c. `rules.decide()` -- what changed

`find_service_area()`'s `"unconfirmed"` status is retired: since areas no
longer hold group data, a known, active area is `"confirmed"` as soon as
it's found (statuses are now just `"confirmed"` / `"inactive"` /
`"not_found"`). `Decision.dispatch_group` was renamed to
`Decision.service_area` and now holds the confirmed area's own label
(e.g. `"Charlotte"`) -- `decide()` has no way to know which technician
(and therefore which dispatch/closing group) will eventually handle a
job; that's resolved later, by a human, at technician assignment.

### 10d. `storage.assign_technician(conn, job_id, technician_id, service_areas)`

The sole entry point for changing a job's assignment. `technician_id=None`
removes the current assignment -- always allowed, no eligibility checks,
since removing is the safe direction.

For a real technician id, checked in order (any failure -> `Handle
Manually`, never a silent partial assignment), reasoning **only** about
service-area identity and technician eligibility, never a group name's
wording:
1. Technician is active.
2. The job's service area is **re-derived fresh** via `find_service_area()`
   against the job's stored city/state -- the exact same lookup
   `rules.decide()` already uses -- and must be `"confirmed"`.
3. The technician's configured `areas` must include that area's label.
4. If the technician has explicit `skills` configured, the job's service
   type must be present *and* match one of them -- missing service-type
   data is itself a block (can't verify), not a silent pass.
5. If the technician has explicit `vehicle_restrictions` configured, the
   job's vehicle must be present and must *not* match one -- same
   missing-data-is-a-block rule.
6. Only once all of the above pass: the selected technician's own
   `dispatch_group`/`closing_group` are surfaced on the result -- opaque
   strings for the dispatcher to use, never reasoned about here.

Every call -- successful or blocked -- writes one permanent `events` row:
new `MessageType.ASSIGNMENT`, `final_action` one of `Assign` / `Reassign`
/ `Remove` / `Handle Manually`. A blocked attempt **never** touches the
job's existing `assigned_technician_id` (a failed reassignment can't
silently clear a working assignment).

`derive_job_status()` extended: `ASSIGNMENT` events preserve the job's
current status (who's assigned is orthogonal to lifecycle stage) unless
`final_action` is `Handle Manually`, which -- as with every other message
type -- always wins via the pre-existing rule 1.

### 10e. UI

- **"Technicians" tab** (9th, appended -- every existing tab index is
  unchanged): add a technician (name, service areas covered, optional
  skills/vehicle restrictions, optional opaque dispatch/closing group),
  list the roster, toggle active/inactive. (While building this, a same-
  script-run staleness bug was caught and fixed: the roster table is
  fetched before the toggle button in script order, so the toggle needs
  `st.rerun()`, with its confirmation message stashed in
  `st.session_state` first since `st.success()` doesn't survive a rerun
  -- the same pattern `simulator.py`'s `sim_pending_delivery` already
  uses for the identical class of problem.)
- **Jobs tab**: list gains an "Assigned" column (technician name only --
  not customer PII). Selecting a job reveals an "Assign technician"
  section: current assignment, a selectbox of active technicians plus
  "(remove assignment)", and an "Apply assignment" button reporting
  either the successful outcome (plus the selected technician's opaque
  dispatch/closing group) or the exact `Handle Manually` reason.
- Process tab's decision display now shows "Service area: X" (was
  "Dispatch group: X") -- consistent with `decide()` no longer claiming
  to know a WhatsApp destination.

### 10f. Not built (explicitly out of scope this phase)

Auto-assignment/suggestion, ETA, technician acceptance/confirmation
workflow, a technician-facing interface of any kind, and (per the
project's standing constraints) anything that would write to Quo. Every
assignment stays local-only, human-initiated, and requires the dispatcher
to separately notify the technician through Quo/WhatsApp themselves. No
real technician or dispatch-group data has been entered yet -- every
example above uses fabricated placeholders.
