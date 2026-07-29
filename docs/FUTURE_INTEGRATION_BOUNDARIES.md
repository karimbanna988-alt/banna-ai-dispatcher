# Future Integration Boundaries

Clean seams for capabilities this system does not have yet, so adding
them later is additive rather than a rewrite. Every seam described here
has exactly one implementation in the current codebase, and that
implementation is inert.

## Quo outbound — `src/dispatcher/adapters.py::QuoOutboundAdapter`

```python
class QuoOutboundAdapter(Protocol):
    def send_acknowledgment(self, conn, *, source, job_id, quo_contact_id, text, approved_by=None) -> OutboundAttemptResult: ...
    def is_enabled(self) -> bool: ...
```

**Only implementation: `DisabledQuoOutboundAdapter`.** It routes every
call through the pre-existing `outbound.propose_send()` boundary
(`QUO_OUTBOUND_MODE` defaults to `"disabled"`; `_send_live()` raises
`NotImplementedError` even in `"live"` mode). This adapter adds **no**
new way to reach Quo — it is a stable interface wrapped around a
capability that was already disabled before this pass, and remains so.
`is_enabled()` reports the real current mode; `DispatcherService` never
assumes it's `True`.

To actually enable sending in the future: implement `_send_live()` in
`outbound.py` (a separate, explicitly-approved change, per that
function's own docstring), which then flows through unchanged — no
`application.py` or UI code would need to change.

## WhatsApp handoff — `src/dispatcher/adapters.py::WhatsAppHandoffAdapter`

```python
class WhatsAppHandoffAdapter(Protocol):
    def prepare(self, text, destination) -> WhatsAppDispatchInstruction: ...
    def confirm_sent(self, conn, *, job_id, technician_id) -> None: ...
```

**Only implementation: `ManualWhatsAppHandoffAdapter`.** `prepare()`
only formats a manual-copy instruction (never opens a browser, never
calls any API — no such capability exists anywhere in this codebase).
`confirm_sent()` only records the dispatcher's own explicit confirmation
via the pre-existing `storage.record_whatsapp_handoff()`. There is no
supported WhatsApp Business API integration in this project, and this
seam does not assume one is coming — it exists so that *if* one is ever
added (a separate, explicitly-approved integration), `DispatcherService`
would take a different adapter instance and nothing else would change.

## AI review — `src/dispatcher/review_interpreter.py::ReviewInterpreter`

```python
class ReviewInterpreter(Protocol):
    def review(self, message, current_job_state) -> ReviewSuggestion: ...
    def is_enabled(self) -> bool: ...
```

**Default implementation: `DisabledReviewInterpreter`.** Always returns
`human_review_required=True`, zero confidence, no recommended action —
structurally incapable of driving an automatic decision.

**Also provided (not wired into `DispatcherService`):
`DeterministicRulesReviewInterpreter`** — repackages
`parser.extract_job()`'s own already-computed output as a
`ReviewSuggestion`. Included to show what a *non-LLM* interpreter looks
like using facts the codebase already computes; explicitly not called
"AI" because it isn't — no model, no inference, no external call.

**No implementation of this protocol may, in this codebase or any
future one built on it:**
- write to SQLite;
- change job status;
- select a technician;
- invent availability, prices, or completion;
- send a message.

`ReviewSuggestion` is advisory-only by construction: nothing in the
current codebase reads one and acts on it. Wiring a suggestion into an
actual `DispatcherService` command (e.g. pre-filling a technician-update
type) would be a separate, explicit, reviewable change — not a side
effect of this pass. No external LLM provider is required for the test
suite or the operational MVP; `tests/test_application_service.py`
proves `DisabledReviewInterpreter` never confirms anything and never
touches storage.

## Configuration validation — `scripts/validate_configuration.py`

Not a "future" seam — already runs today, read-only, against the real
database or any other SQLite file, and never writes (opens the
connection with SQLite's `mode=ro` URI flag, so an attempted write
raises rather than silently succeeding). Intended to be run before any
operational session and whenever the technician roster changes. See
that script's own module docstring for usage.

## Source adapters — `src/dispatcher/source_adapters.py`

Already fully wired into the real pipeline (composes the pre-existing
`companies/` registry and `policy.py` table — no new parsing logic, no
new pipeline). Listed here because it's the boundary a genuinely new
source company would extend: add a `companies/<new_source>.py` profile,
add its `policy.py` entries, add a `SourceAdapter` instance in
`source_adapters.py`. No pipeline duplication required — one shared
pipeline, one more small adapter.

## API/CLI adapters (not built)

`DispatcherService`'s command methods take plain Python types
(`int`, `str`, dataclass commands) and return a plain dataclass
(`ActionResult`) — no Streamlit type appears in any command signature.
A future CLI (`scripts/dispatch_cli.py`, hypothetical) or HTTP API
adapter would construct a `DispatcherService` the same way
`luxor_desk_pages/*.py` does and call the same methods. Not built in
this pass — named here only to record that the layering already
supports it without further refactoring.
