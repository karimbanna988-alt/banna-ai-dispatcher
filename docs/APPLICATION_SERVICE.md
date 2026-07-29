# Application Service (`src/dispatcher/application.py`)

## What it is

`DispatcherService` is a plain Python class wrapping one
`sqlite3.Connection` (mirrors `conn = storage.connect()`, called once
per Streamlit page render). It has no import-time Streamlit dependency
and no SQL of its own. Construct it, call a command, get an
`ActionResult` back.

```python
from dispatcher import storage
from dispatcher.application import DispatcherService
from dispatcher.app_models import TechnicianUpdateCommand

conn = storage.connect()
service = DispatcherService(conn)
result = service.record_technician_update(
    TechnicianUpdateCommand(job_id=42, update_type="Going", raw_update="omw now")
)
if result.success:
    print(result.current_status)  # "In Progress"
```

## Commands (state-changing)

| Method | Storage call(s) it wraps | Mapped UI action |
|---|---|---|
| `acknowledge_job_manually(message_id)` | `mark_acknowledged` | "MARK K SENT MANUALLY" |
| `attempt_quo_acknowledgment(...)` | `mark_acknowledged` + `DisabledQuoOutboundAdapter` | "SEND K" (APPROVAL/LIVE mode only — dead in this deployment since outbound stays disabled) |
| `prepare_technician_dispatch(message_id)` | `mark_copied` + `mark_whatsapp_opened` | "DISPATCH IN WHATSAPP" |
| `confirm_whatsapp_dispatch(...)` | `save_audit_entry` + `mark_quo_pending_message_actioned` + `mark_dispatched_manually` + `ManualWhatsAppHandoffAdapter.confirm_sent` | "CONFIRM DISPATCHED" |
| `reject_pending_message(...)` | `save_audit_entry` + `mark_quo_pending_message_actioned` | "Handle Manually" / "Pass / Cancel Job" (New Jobs, pending-message level) |
| `record_technician_update(command)` | `storage.record_technician_update` | "Save technician update" |
| `prepare_company_update(...)` | `updates.reply_for_update_type` + `storage.mark_company_update_prepared` | "PREPARE COMPANY UPDATE" |
| `confirm_company_update_sent(...)` | `storage.mark_company_update_sent_manually` | "MARK UPDATE SENT MANUALLY" |
| `pass_or_cancel_job(...)` | `storage.pass_or_cancel_job` | "PASS / CANCEL JOB" (Active Jobs, already-committed job) |
| `record_closing(command)` | `storage.record_job_closing` | "RECORD CLOSING" |
| `prepare_company_closing(job_id)` | `closing.render_closing_from_persisted_fields` + `storage.mark_company_closing_prepared` | "PREPARE COMPANY CLOSING" |
| `confirm_company_closing_sent(...)` | `storage.mark_company_closing_sent_manually` | "MARK CLOSING SENT MANUALLY" |
| `complete_job(job_id)` | `storage.complete_job` | "COMPLETE JOB" |

## Queries (read-only)

| Method | Notes |
|---|---|
| `get_job_workspace(job_id)` | Pure pass-through to `storage.get_job_by_id`. No Streamlit dependency. |
| `get_job_history(job_id)` | Pure pass-through to `storage.get_audit_timeline_by_job_id`. No Streamlit dependency. |
| `get_queue(kind)` | `kind` in `{"new_jobs", "active", "closings", "completed"}`. **Updated this round**: no longer imports `luxor_desk_read_model` itself. Delegates to an injected `QueueReader` (a `Protocol` defined in `application.py`); raises `RuntimeError` if none was provided. The concrete implementation, `luxor_desk_read_model.LuxorDeskQueueReader`, lives at the repo root and is constructed by the UI layer — `application.py` has zero import-time or call-time dependency on any `luxor_desk_*` module, enforced by `tests/test_dependency_boundaries.py`. See `docs/ARCHITECTURE_VERIFICATION_131ABCE.md`. |

## Important design decision: preview vs. prepare

`prepare_company_update`/`prepare_company_closing` are **write**
commands — calling them records a `Company Update Prepared`/`Company
Closing Prepared` audit event. They must only ever be called from
inside a button's `if st.button(...):` block, never on a bare page
render (that would violate read-model purity — see
`tests/test_read_model_purity.py`).

The text shown on screen *before* the dispatcher clicks "PREPARE …" — so
they can see what they're about to send — comes from calling the
underlying pure formatter directly (`updates.reply_for_update_type()`,
`closing.render_closing_from_persisted_fields()`), not from the service
command. This is intentional: the preview must be side-effect-free and
safe to compute on every render; the "prepare" command is the one that
records that a human decided to actually prepare it.

## `ActionResult`

Every command returns one of these (see `src/dispatcher/app_models.py`):

```python
@dataclass
class ActionResult:
    success: bool
    action: str
    job_id: Optional[int]
    previous_status: Optional[str]
    current_status: Optional[str]
    prepared_text: Optional[str]
    validation_issues: List[ValidationIssue]
    human_review_required: bool
    human_review_reason: Optional[HumanReviewReason]
    audit_event_id: Optional[int]
    idempotent_replay: bool
    user_message: str
```

`success=False` covers two different things a caller should
distinguish: **bad input** (`validation_issues` non-empty — e.g. a
blank reply, a missing ETA) and **illegal state transition**
(`user_message` explains it — e.g. completing a job with no closing
recorded). Neither ever raises an uncaught exception up to the UI layer
for ordinary bad input; `InvalidJobActionError` from `storage.py` is
caught inside every command and converted to a failed `ActionResult`.

## Idempotency

See `docs/EVENT_CONTRACT.md` for the mechanism. From the service's
point of view: every write command computes a deterministic
`idempotency_key` (job_id/message_id + action + a content fingerprint)
before calling storage, checks whether an event with that key already
exists, and reports `idempotent_replay=True` if so. The storage layer
independently re-checks the same key at write time — so even if two
`DispatcherService` calls raced (two Streamlit reruns), only one event
is ever created; the second's `ActionResult` may occasionally read
`idempotent_replay=False` in that exact race window (a cosmetic
edge case, not a duplicate-write risk) since it reflects the pre-check,
not the storage-layer guarantee.

## Adapters injected, never hardcoded

`DispatcherService.__init__` accepts `whatsapp_adapter` and
`quo_adapter`, defaulting to `ManualWhatsAppHandoffAdapter()` and
`DisabledQuoOutboundAdapter()` (see
`docs/FUTURE_INTEGRATION_BOUNDARIES.md`). A future test or a future
real adapter can be swapped in without touching any command's logic.
