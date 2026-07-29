"""One deterministic end-to-end harness proving the full canonical
workflow (Quo job -> acknowledgment -> LUXOR message -> WhatsApp
dispatch -> technician update -> company update -> closing -> company
closing -> completion -> audit history) across AGN, Lucky, and Main
Leads, plus the edge cases the spec calls out explicitly. Every job is
fabricated test data in a temporary SQLite database -- never the real
database, never a real network call.

Edge cases covered elsewhere and cross-referenced here rather than
duplicated:
  - exact duplicate inbound / correction on same ticket:
    tests/test_storage.py (classify_incoming), tests/test_read_model_purity.py-adjacent
    tests/test_luxor_desk_real.py::test_correction_for_same_ticket_stays_separately_actionable
  - two browser-session-equivalent commands / poller retry:
    tests/test_concurrency.py, tests/test_application_service.py (idempotent replay)
  - stale schema: tests/test_schema_guard_ui.py, tests/test_schema_migration.py
  - database contention: tests/test_concurrency.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dispatcher import storage
from dispatcher.application import DispatcherService
from dispatcher.app_models import ClosingCommand, TechnicianUpdateCommand
from dispatcher.parser import extract_job, load_vehicle_rules
from dispatcher.storage import InvalidJobActionError


@pytest.fixture(scope="module")
def vehicle_rules():
    return load_vehicle_rules()


def _commit_new_job(conn, vehicle_rules, raw: str) -> int:
    job = extract_job(raw, vehicle_rules)
    cr = storage.classify_incoming(conn, job)
    return storage.save_audit_entry(conn, job, cr, final_action="Approve")


def _agn_message(ticket: str) -> str:
    return (
        f"Ref: AGN Locksmith Services\nPDL: {ticket}\nPh: 1-555-010-3000\n"
        f"Addr: 100 Fake St, Charlotte, NC\nDesc: Car Lockout\n"
    )


def _lucky_message(ticket: str) -> str:
    return (
        f"Co: LUCKY LOCKSMITH LLC\nPDL: {ticket}\nPh: 1-555-010-4000\n"
        f"Addr: 200 Fake Ave, Charlotte, NC\nDesc: Car Lockout\n"
    )


def _main_leads_message(ticket: str) -> str:
    return (
        f"Power By Ucallz App\n\nFabricated Locksmith Pro\nTicket: {ticket}\n"
        f"Name: Test Person\nPhone: (555) 010-2000\nAddress: 300 Fake Dr, Charlotte, NC 28202\n"
        f"Desc: Car Lockout\n"
    )


def _full_supervised_lifecycle(conn, service, job_id, closing_supported=True):
    """Runs the complete manual lifecycle for one already-dispatched job
    and returns the final job state + audit history."""
    # acknowledgment preparation + manual confirmation is handled at the
    # pending-message stage (dispatch_tracking), not modeled here since
    # this harness starts from an already-committed job -- see
    # tests/test_luxor_desk_real.py for the pending-message-level ack
    # flow (COPY K / MARK K SENT MANUALLY / DISPATCH IN WHATSAPP /
    # CONFIRM DISPATCHED), already covered end to end there.

    update_result = service.record_technician_update(
        TechnicianUpdateCommand(job_id=job_id, update_type="Going", raw_update="omw now")
    )
    assert update_result.success

    prepare_update = service.prepare_company_update(job_id, "Going")
    assert prepare_update.success
    sent_update = service.confirm_company_update_sent(job_id, prepare_update.prepared_text)
    assert sent_update.success

    completed_update = service.record_technician_update(
        TechnicianUpdateCommand(job_id=job_id, update_type="Completed", raw_update="job done, customer happy")
    )
    assert completed_update.success

    closing_result = service.record_closing(
        ClosingCommand(job_id=job_id, raw_closing="Closed $150 CC", final_amount="150", payment_method="CC")
    )
    assert closing_result.success
    assert closing_result.current_status == "Closed"

    prepare_closing = service.prepare_company_closing(job_id)
    if not closing_supported:
        assert prepare_closing.success is False
        assert prepare_closing.human_review_required is True
        return service.get_job_workspace(job_id), service.get_job_history(job_id)

    assert prepare_closing.success
    sent_closing = service.confirm_company_closing_sent(job_id, prepare_closing.prepared_text)
    assert sent_closing.success

    complete_result = service.complete_job(job_id)
    assert complete_result.success
    assert complete_result.current_status == "Closed"

    workspace = service.get_job_workspace(job_id)
    history = service.get_job_history(job_id)
    return workspace, history


# ---------------------------------------------------------------------- AGN
def test_agn_full_supervised_lifecycle(tmp_path, vehicle_rules):
    db_path = tmp_path / "e2e_agn.db"
    conn = storage.connect(db_path)
    job_id = _commit_new_job(conn, vehicle_rules, _agn_message("E2EAGN1"))
    service = DispatcherService(conn)

    workspace, history = _full_supervised_lifecycle(conn, service, job_id)

    assert workspace["reconciliation_state"] == "Settled"
    assert workspace["status"] == "Closed"
    final_actions = [e["final_action"] for e in history]
    assert "Company Update Prepared" in final_actions
    assert "Company Update Sent Manually" in final_actions
    assert "Company Closing Prepared" in final_actions
    assert "Company Closing Sent Manually" in final_actions
    assert "Job Completed" in final_actions
    conn.close()


# -------------------------------------------------------------------- Lucky
def test_lucky_full_supervised_lifecycle(tmp_path, vehicle_rules):
    db_path = tmp_path / "e2e_lucky.db"
    conn = storage.connect(db_path)
    job_id = _commit_new_job(conn, vehicle_rules, _lucky_message("E2ELUCKY1"))
    service = DispatcherService(conn)

    workspace, history = _full_supervised_lifecycle(conn, service, job_id)

    assert workspace["reconciliation_state"] == "Settled"
    assert workspace["source"] == "Lucky"
    conn.close()


# ---------------------------------------------------------------- Main Leads
def test_main_leads_lifecycle_no_ack_closing_unsupported(tmp_path, vehicle_rules):
    """Main Leads: no acknowledgment applies at the source-adapter level
    (see source_adapters.MAIN_LEADS_ADAPTER), dispatched directly, and
    closing is genuinely unsupported per policy -- the lifecycle must
    surface that as human_review_required, never a fabricated closing
    message."""
    from dispatcher.source_adapters import MAIN_LEADS_ADAPTER

    assert MAIN_LEADS_ADAPTER.acknowledgment_required_for_new_job() is False
    assert MAIN_LEADS_ADAPTER.closing_supported is False

    db_path = tmp_path / "e2e_mainleads.db"
    conn = storage.connect(db_path)
    job_id = _commit_new_job(conn, vehicle_rules, _main_leads_message("E2EML1"))
    service = DispatcherService(conn)

    workspace, history = _full_supervised_lifecycle(conn, service, job_id, closing_supported=False)
    assert workspace["source"] == "Main Leads"
    # Closing was still *recorded* (raw technician closing persisted) --
    # only the company-closing template is unsupported.
    assert any(e["message_type"] == "CLOSING" for e in history)
    conn.close()


# --------------------------------------------------------------- Edge cases
def test_cancellation_after_dispatch(tmp_path, vehicle_rules):
    db_path = tmp_path / "e2e_cancel.db"
    conn = storage.connect(db_path)
    job_id = _commit_new_job(conn, vehicle_rules, _agn_message("E2ECANCEL1"))
    service = DispatcherService(conn)

    result = service.pass_or_cancel_job(job_id, "Cancel", "customer no longer needs service")
    assert result.success
    assert result.current_status == "Cancelled"

    # Closing a cancelled job must fail safely, not corrupt anything.
    closing_attempt = service.record_closing(ClosingCommand(job_id=job_id, raw_closing="Closed $150 CC"))
    assert closing_attempt.success is False
    conn.close()


def test_closing_without_known_dispatch_is_handled_by_classify_incoming(tmp_path, vehicle_rules):
    """A closing-looking message with no prior committed job for its
    ticket is classified CLOSING_WITHOUT_DISPATCH -- an anomaly requiring
    manual handling, never treated as a normal new job or silently
    dropped."""
    conn = storage.connect(tmp_path / "e2e_noclosing.db")
    job = extract_job(
        "Ref: AGN Locksmith Services\nPDL: NEVERDISPATCHED\nPh: 1-555-010-3000\n"
        "Addr: 100 Fake St, Charlotte, NC\nDesc: Car Lockout\nClosed $150 CC\n",
        vehicle_rules,
    )
    cr = storage.classify_incoming(conn, job)
    assert cr.message_type.value == "CLOSING_WITHOUT_DISPATCH"
    conn.close()


def test_ambiguous_technician_update_requires_human_review(tmp_path, vehicle_rules):
    db_path = tmp_path / "e2e_ambiguous.db"
    conn = storage.connect(db_path)
    job_id = _commit_new_job(conn, vehicle_rules, _agn_message("E2EAMBIG1"))
    service = DispatcherService(conn)

    result = service.record_technician_update(
        TechnicianUpdateCommand(job_id=job_id, update_type="Custom", raw_update="car had a weird smell, unsure what to do")
    )
    assert result.success
    assert result.human_review_required is True
    assert result.current_status == "Needs Review"
    conn.close()


def test_missing_whatsapp_destination_never_invented(tmp_path):
    """No technician configured -> no eligible technician -> no
    destination -- never fabricated. Exercised via the configuration
    validator on a database with zero technicians."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import validate_configuration

    conn = storage.connect(tmp_path / "e2e_nodest.db")
    findings = validate_configuration.validate(conn)
    conn.close()
    assert any("No active technicians" in f.message for f in findings)


def test_unsupported_source_gets_safe_default_adapter(tmp_path):
    from dispatcher.source_adapters import get_source_adapter

    adapter = get_source_adapter("SomeFutureUnconfiguredSource")
    assert adapter.requires_k is False
    assert adapter.closing_supported is False


# ------------------------------------------------- process restart proof
def test_lifecycle_survives_a_process_restart_halfway_through(tmp_path, vehicle_rules):
    """Proves no required workflow state exists only in memory/session
    state: closes the connection (simulating the process exiting) after
    the first half of the lifecycle, reopens a brand new connection and
    DispatcherService (simulating a fresh process start), and completes
    the rest -- the second half must work exactly as if it were the same
    process."""
    db_path = tmp_path / "e2e_restart.db"

    conn1 = storage.connect(db_path)
    job_id = _commit_new_job(conn1, vehicle_rules, _agn_message("E2ERESTART1"))
    service1 = DispatcherService(conn1)
    r1 = service1.record_technician_update(TechnicianUpdateCommand(job_id=job_id, update_type="Going", raw_update="omw now"))
    assert r1.success
    r2 = service1.prepare_company_update(job_id, "Going")
    assert r2.success
    conn1.close()  # simulate process exit

    # Simulate process restart: brand new connection, brand new service
    # instance, same file.
    conn2 = storage.connect(db_path)
    service2 = DispatcherService(conn2)
    workspace = service2.get_job_workspace(job_id)
    assert workspace["status"] == "In Progress"  # survived the "restart"

    r3 = service2.confirm_company_update_sent(job_id, "Going")
    assert r3.success
    r4 = service2.record_closing(ClosingCommand(job_id=job_id, raw_closing="Closed $150 CC", final_amount="150", payment_method="CC"))
    assert r4.success
    r5 = service2.prepare_company_closing(job_id)
    assert r5.success
    r6 = service2.confirm_company_closing_sent(job_id, r5.prepared_text)
    assert r6.success
    r7 = service2.complete_job(job_id)
    assert r7.success
    assert r7.current_status == "Closed"
    conn2.close()

    # Yet another restart -- repeated commands after restart must remain
    # safely idempotent, not duplicate anything.
    conn3 = storage.connect(db_path)
    service3 = DispatcherService(conn3)
    events_before = len(service3.get_job_history(job_id))
    r8 = service3.complete_job(job_id)  # already completed -- must be a safe idempotent no-op
    assert r8.success
    assert r8.idempotent_replay is True
    events_after = len(service3.get_job_history(job_id))
    assert events_before == events_after
    conn3.close()


def test_restart_and_reopen_same_db_reports_consistent_health(tmp_path, vehicle_rules):
    from dispatcher import health

    db_path = tmp_path / "e2e_health_restart.db"
    conn1 = storage.connect(db_path)
    _commit_new_job(conn1, vehicle_rules, _agn_message("E2EHEALTH1"))
    health1 = health.get_operational_health(conn1)
    conn1.close()

    conn2 = storage.connect(db_path)
    health2 = health.get_operational_health(conn2)
    conn2.close()

    assert health1.current_schema_revision == health2.current_schema_revision
    assert health1.migration_required == health2.migration_required is False
