"""Focused tests for the application service layer (application.py) and
its supporting modules: app_models, action_names, source_adapters,
adapters, review_interpreter.

Every job used here is created via the real NEW-job pipeline
(extract_job/classify_incoming/save_audit_entry), exactly like a real
job would already exist before DispatcherService is ever called for it
-- never a fabricated ExtractedJob, never a fabricated runtime record.
All connections are temporary on-disk SQLite (tmp_path); nothing here
touches the real database or makes a real network call.
"""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

from dispatcher import storage
from dispatcher.action_names import ActionName, TECHNICIAN_UPDATE_TYPES
from dispatcher.adapters import DisabledQuoOutboundAdapter, ManualWhatsAppHandoffAdapter
from dispatcher.app_models import ActionResult, ClosingCommand, TechnicianUpdateCommand, ValidationIssue
from dispatcher.application import DispatcherService, compute_idempotency_key
from dispatcher.parser import extract_job, load_vehicle_rules
from dispatcher.review_interpreter import DisabledReviewInterpreter, ReviewSuggestion
from dispatcher.source_adapters import (
    AGN_ADAPTER, LUCKY_ADAPTER, LUCKY_CLOSINGS_REPLIES_ADAPTER, MAIN_LEADS_ADAPTER, get_source_adapter,
)
from dispatcher.storage import InvalidJobActionError, MessageType

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture()
def conn(tmp_path):
    return storage.connect(tmp_path / "test.db")


@pytest.fixture(scope="module")
def vehicle_rules():
    return load_vehicle_rules()


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def commit_job(conn: sqlite3.Connection, vehicle_rules, raw: str) -> int:
    job = extract_job(raw, vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    return storage.save_audit_entry(conn, job, classify_result, final_action="Approve")


@pytest.fixture()
def job_id(conn, vehicle_rules) -> int:
    return commit_job(conn, vehicle_rules, load("agn_clean.txt"))


@pytest.fixture()
def service(conn) -> DispatcherService:
    return DispatcherService(conn)


# ---------------------------------------------------------- ActionResult -
def test_record_technician_update_returns_structured_action_result(service, job_id):
    result = service.record_technician_update(
        TechnicianUpdateCommand(job_id=job_id, update_type="Going", raw_update="omw now")
    )
    assert isinstance(result, ActionResult)
    assert result.success is True
    assert result.job_id == job_id
    assert result.current_status == "In Progress"
    assert result.idempotent_replay is False


def test_custom_update_marks_human_review_required(service, job_id):
    result = service.record_technician_update(
        TechnicianUpdateCommand(job_id=job_id, update_type="Custom", raw_update="weird noise, unsure")
    )
    assert result.success is True
    assert result.human_review_required is True
    assert result.human_review_reason is not None
    assert result.human_review_reason.code == "ambiguous_update"


def test_invalid_input_returns_validation_issues_not_an_exception(service, job_id):
    result = service.record_technician_update(TechnicianUpdateCommand(job_id=job_id, update_type="Going", raw_update="   "))
    assert result.success is False
    assert any(isinstance(i, ValidationIssue) and i.field == "raw_update" for i in result.validation_issues)


def test_eta_without_minutes_is_a_validation_issue(service, job_id):
    result = service.record_technician_update(TechnicianUpdateCommand(job_id=job_id, update_type="ETA", raw_update="eta 15"))
    assert result.success is False
    assert any(i.field == "eta_minutes" for i in result.validation_issues)


# ----------------------------------------------------------- Idempotency -
def test_double_click_technician_update_is_idempotent(service, job_id):
    cmd = TechnicianUpdateCommand(job_id=job_id, update_type="Going", raw_update="omw now")
    r1 = service.record_technician_update(cmd)
    r2 = service.record_technician_update(cmd)
    assert r1.idempotent_replay is False
    assert r2.idempotent_replay is True
    assert r2.current_status == r1.current_status


def test_distinct_technician_updates_both_recorded_not_collapsed(service, job_id):
    """A second, genuinely different technician update (e.g. a later
    "No Answer" after an earlier "Going") must never be treated as a
    replay of the first, even though it shares job_id -- only the exact
    same (job_id, action, content) collapses."""
    r1 = service.record_technician_update(TechnicianUpdateCommand(job_id=job_id, update_type="Going", raw_update="omw now"))
    r2 = service.record_technician_update(TechnicianUpdateCommand(job_id=job_id, update_type="No Answer", raw_update="called, no answer"))
    assert r1.idempotent_replay is False
    assert r2.idempotent_replay is False
    events = service.get_job_history(job_id)
    status_events = [e for e in events if e["message_type"] == MessageType.STATUS.value]
    assert len(status_events) == 2


def test_double_click_prepare_company_update_is_idempotent(service, job_id):
    service.record_technician_update(TechnicianUpdateCommand(job_id=job_id, update_type="Going", raw_update="omw now"))
    r1 = service.prepare_company_update(job_id, "Going")
    r2 = service.prepare_company_update(job_id, "Going")
    assert r1.idempotent_replay is False
    assert r2.idempotent_replay is True
    events = service.get_job_history(job_id)
    prepared = [e for e in events if e["final_action"] == ActionName.COMPANY_UPDATE_PREPARED.value]
    assert len(prepared) == 1


def test_double_click_complete_job_is_idempotent(service, job_id):
    service.record_closing(ClosingCommand(job_id=job_id, raw_closing="Closed $150 CC", final_amount="150", payment_method="CC"))
    r1 = service.complete_job(job_id)
    r2 = service.complete_job(job_id)
    assert r1.success and r2.success
    assert r2.idempotent_replay is True
    events = service.get_job_history(job_id)
    completed = [e for e in events if e["final_action"] == ActionName.JOB_COMPLETED.value]
    assert len(completed) == 1


def test_repeated_pass_cancel_after_first_success_fails_safely(service, job_id):
    r1 = service.pass_or_cancel_job(job_id, "Cancel", "customer cancelled")
    assert r1.success is True
    r2 = service.pass_or_cancel_job(job_id, "Cancel", "customer cancelled")
    assert r2.success is False  # illegal transition, not a crash
    assert "already" in (r2.user_message or "").lower()


# ------------------------------------------------------- Illegal transitions
def test_completion_before_closing_fails_safely(service, job_id):
    result = service.complete_job(job_id)
    assert result.success is False
    assert result.job_id == job_id


def test_cancellation_after_completion_fails_safely(service, job_id):
    service.record_closing(ClosingCommand(job_id=job_id, raw_closing="Closed $150 CC", final_amount="150", payment_method="CC"))
    service.complete_job(job_id)
    result = service.pass_or_cancel_job(job_id, "Cancel", "too late")
    assert result.success is False


def test_closing_after_cancellation_fails_safely(service, job_id):
    service.pass_or_cancel_job(job_id, "Cancel", "customer cancelled")
    result = service.record_closing(ClosingCommand(job_id=job_id, raw_closing="Closed $150 CC"))
    assert result.success is False


def test_missing_closing_fields_surface_as_human_review(service, job_id):
    service.record_closing(ClosingCommand(job_id=job_id, raw_closing="Closed, amount unclear"))
    result = service.prepare_company_closing(job_id)
    assert result.success is False
    assert result.human_review_required is True
    assert result.human_review_reason.code == "missing_closing_fields"


def test_unsupported_company_closing_surfaces_as_human_review(conn, vehicle_rules, service):
    job_id = commit_job(conn, vehicle_rules, load("main_leads_clean.txt"))
    service.record_closing(ClosingCommand(job_id=job_id, raw_closing="Closed $150 CC", final_amount="150", payment_method="CC"))
    result = service.prepare_company_closing(job_id)
    assert result.success is False
    assert result.human_review_reason.code == "unsupported_closing"


# --------------------------------------------------- No partial writes ---
def test_illegal_transition_leaves_no_partial_event(service, job_id):
    before = len(service.get_job_history(job_id))
    service.complete_job(job_id)  # illegal -- no closing yet
    after = len(service.get_job_history(job_id))
    assert before == after  # nothing was written for the rejected attempt


# --------------------------------------------------------------- Full flow
def test_full_lifecycle_via_service_matches_storage_layer(service, job_id):
    assert service.record_technician_update(
        TechnicianUpdateCommand(job_id=job_id, update_type="Completed", raw_update="job done")
    ).success
    assert service.prepare_company_update(job_id, "Completed").success
    prepared = service.prepare_company_update(job_id, "Completed")
    assert service.confirm_company_update_sent(job_id, prepared.prepared_text).success
    assert service.record_closing(
        ClosingCommand(job_id=job_id, raw_closing="Closed $150 CC", final_amount="150", payment_method="CC")
    ).success
    prep_closing = service.prepare_company_closing(job_id)
    assert prep_closing.success
    assert service.confirm_company_closing_sent(job_id, prep_closing.prepared_text).success
    final = service.complete_job(job_id)
    assert final.success
    assert final.current_status == "Closed"

    workspace = service.get_job_workspace(job_id)
    assert workspace["reconciliation_state"] == "Settled"


# ---------------------------------------------------------- Source adapters
def test_agn_and_lucky_require_k(service):
    assert AGN_ADAPTER.acknowledgment_required_for_new_job() is True
    assert AGN_ADAPTER.acknowledgment_text_for_new_job() == "K"
    assert LUCKY_ADAPTER.acknowledgment_required_for_new_job() is True
    assert LUCKY_ADAPTER.acknowledgment_text_for_new_job() == "K"


def test_main_leads_does_not_require_ack():
    assert MAIN_LEADS_ADAPTER.acknowledgment_required_for_new_job() is False
    assert MAIN_LEADS_ADAPTER.closing_supported is False


def test_closings_and_replies_is_not_a_fifth_company():
    assert LUCKY_CLOSINGS_REPLIES_ADAPTER.source is None
    assert LUCKY_CLOSINGS_REPLIES_ADAPTER.acknowledgment_required_for_new_job() is False
    assert LUCKY_CLOSINGS_REPLIES_ADAPTER.closing_supported is False


def test_agn_and_lucky_support_closings():
    assert AGN_ADAPTER.closing_supported is True
    assert LUCKY_ADAPTER.closing_supported is True


def test_unknown_stream_key_gets_safe_default_adapter():
    adapter = get_source_adapter("some-unconfigured-stream")
    assert adapter.requires_k is False
    assert adapter.closing_supported is False
    assert adapter.acknowledgment_required_for_new_job() is False


# ------------------------------------------------------- Disabled adapters
def test_disabled_quo_outbound_adapter_never_sends(conn, monkeypatch):
    monkeypatch.delenv("QUO_OUTBOUND_MODE", raising=False)
    adapter = DisabledQuoOutboundAdapter()
    assert adapter.is_enabled() is False
    result = adapter.send_acknowledgment(conn, source="AGN", job_id=None, quo_contact_id="c1", text="K")
    assert result.outcome == "blocked"
    count = conn.execute("SELECT COUNT(*) FROM quo_outbound_log").fetchone()[0]
    assert count == 1  # attempt recorded, never sent
    row = conn.execute("SELECT outcome FROM quo_outbound_log LIMIT 1").fetchone()
    assert row[0] == "blocked"


def test_manual_whatsapp_adapter_never_claims_delivery_without_confirmation(conn, job_id):
    adapter = ManualWhatsAppHandoffAdapter()
    instruction = adapter.prepare("some message", destination=None)
    assert instruction.destination_configured is False
    assert "manually" in instruction.instruction.lower()
    # confirm_sent only records a human's own confirmation -- never sends.
    adapter.confirm_sent(conn, job_id=job_id, technician_id=None)
    handoffs = storage.get_whatsapp_handoffs(conn, job_id)
    assert len(handoffs) == 1


def test_disabled_review_interpreter_never_confirms(conn, job_id):
    interpreter = DisabledReviewInterpreter()
    assert interpreter.is_enabled() is False
    suggestion = interpreter.review("some message", {"id": job_id})
    assert isinstance(suggestion, ReviewSuggestion)
    assert suggestion.human_review_required is True
    assert suggestion.confidence == 0
    assert suggestion.recommended_final_action is None


def test_review_interpreter_has_no_storage_access():
    """Structural guard: review_interpreter.py must never import
    storage.py -- an interpreter that cannot import it cannot write to
    SQLite, select a technician, or change status, by construction."""
    source = pathlib.Path("src/dispatcher/review_interpreter.py").read_text(encoding="utf-8")
    top_level_imports = [
        line for line in source.splitlines()
        if line.strip().startswith(("import ", "from ")) and not line.strip().startswith(("import ", "from ")) is False
    ]
    assert "from .storage import" not in source
    assert "from . import storage" not in source
    assert "import storage" not in source.replace("from .storage", "")


# -------------------------------------------------- No competing status ---
def test_no_competing_status_derivation_in_application_layer():
    """application.py must never assign a status literal itself or
    reimplement derive_job_status()'s branching -- every status comes
    back from storage.py's own functions. This greps for the tell-tale
    patterns a competing state machine would need."""
    source = pathlib.Path("src/dispatcher/application.py").read_text(encoding="utf-8")
    assert "def derive_job_status" not in source
    assert "status =" not in source.replace("current_status", "").replace("previous_status", "")
    for forbidden_literal in ('"New"', '"Approved"', '"In Progress"', '"Closed"', '"Cancelled"', '"Rejected"'):
        assert forbidden_literal not in source, f"application.py must not spell status literal {forbidden_literal} itself"


def test_application_layer_contains_no_sql():
    source = pathlib.Path("src/dispatcher/application.py").read_text(encoding="utf-8")
    for forbidden in ("SELECT ", "INSERT INTO", "UPDATE ", "DELETE FROM", "conn.execute("):
        assert forbidden not in source, f"application.py must not contain SQL/raw execute: {forbidden!r}"


def test_application_layer_uses_canonical_action_names():
    source = pathlib.Path("src/dispatcher/application.py").read_text(encoding="utf-8")
    assert "ActionName.APPROVE" in source
    # Spot check: none of the operational-note literals are spelled
    # independently in application.py -- they come from ActionName.
    for literal in ("Company Update Prepared", "Company Closing Sent Manually", "Job Completed"):
        assert literal not in source


# ----------------------------------------------------- Idempotency keys --
def test_compute_idempotency_key_is_deterministic():
    k1 = compute_idempotency_key(42, "record_technician_update", "Going|None|omw now")
    k2 = compute_idempotency_key(42, "record_technician_update", "Going|None|omw now")
    assert k1 == k2


def test_compute_idempotency_key_tolerant_of_whitespace_formatting():
    k1 = compute_idempotency_key(42, "action", "Going  now")
    k2 = compute_idempotency_key(42, "action", "going now")
    assert k1 == k2


def test_compute_idempotency_key_distinct_for_distinct_content():
    k1 = compute_idempotency_key(42, "action", "Going now")
    k2 = compute_idempotency_key(42, "action", "No answer")
    assert k1 != k2


# --------------------------------------------------------- Audit contract
def test_events_have_schema_version(conn, job_id):
    row = conn.execute("SELECT schema_version FROM events WHERE job_id = ? LIMIT 1", (job_id,)).fetchone()
    assert row[0] == storage.EVENT_SCHEMA_VERSION


def test_events_idempotency_key_column_exists(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    assert "idempotency_key" in columns
    assert "schema_version" in columns


def test_get_event_by_idempotency_key_roundtrip(service, job_id):
    service.record_technician_update(TechnicianUpdateCommand(job_id=job_id, update_type="Going", raw_update="omw now"))
    key = compute_idempotency_key(job_id, "record_technician_update", "Going|None|omw now")
    event = storage.get_event_by_idempotency_key(service.conn, key)
    assert event is not None
    assert event["job_id"] == job_id


# ---------------------------------------------------------- Safety guards
def test_no_secrets_in_application_layer_source():
    for f in (
        "src/dispatcher/application.py", "src/dispatcher/adapters.py",
        "src/dispatcher/source_adapters.py", "src/dispatcher/review_interpreter.py",
    ):
        source = pathlib.Path(f).read_text(encoding="utf-8")
        assert "QUO_API_KEY" not in source or "os.environ" in source
        assert "sk-" not in source


def test_no_real_network_imports_in_application_layer():
    for f in ("src/dispatcher/application.py", "src/dispatcher/adapters.py"):
        source = pathlib.Path(f).read_text(encoding="utf-8")
        assert "import requests" not in source
        assert "urllib" not in source
