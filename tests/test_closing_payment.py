"""Tests for the minimum structured closing/reconciliation record added to
the existing jobs/events tables (no new tables): save_audit_entry()'s new
optional closing-detail parameters, mark_job_settled(), and
get_latest_closing_details(). reconciliation_state is deliberately
separate from jobs.status/derive_job_status() -- these tests confirm that
separation holds."""

from pathlib import Path

import pytest

from dispatcher import storage
from dispatcher.parser import extract_job, load_vehicle_rules
from dispatcher.storage import ClassifyResult, MessageType

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def conn(tmp_path):
    return storage.connect(tmp_path / "test.db")


@pytest.fixture(scope="module")
def vehicle_rules():
    return load_vehicle_rules()


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def seed_approved_job(conn, vehicle_rules, ticket="FAKE1"):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    job_id = storage.save_audit_entry(conn, job, classify_result, final_action="Approve")
    return job_id, job


def close_job(conn, job_id, job, **closing_kwargs):
    classify_result = ClassifyResult(MessageType.CLOSING, existing_job_id=job_id, reason="dispatcher-entered closing")
    storage.save_audit_entry(
        conn, job, classify_result, luxor_message="Closed -- $120 CC", final_action="Approve", **closing_kwargs,
    )


# --------------------------------------------------- Structured closing --
def test_closing_amount_is_stored_verbatim_on_the_event(conn, vehicle_rules):
    job_id, job = seed_approved_job(conn, vehicle_rules)
    close_job(
        conn, job_id, job, closing_amount="120.00", closing_payment_method="CC",
        closing_parts_amount="15.00", closing_company_share="80.00", closing_technician_share="40.00",
    )

    row = conn.execute(
        "SELECT closing_amount, closing_payment_method, closing_parts_amount, "
        "closing_company_share, closing_technician_share FROM events WHERE message_type = 'CLOSING'"
    ).fetchone()
    assert row == ("120.00", "CC", "15.00", "80.00", "40.00")


def test_closing_details_default_to_null_when_not_supplied(conn, vehicle_rules):
    job_id, job = seed_approved_job(conn, vehicle_rules)
    close_job(conn, job_id, job)  # no closing_* kwargs at all

    row = conn.execute(
        "SELECT closing_amount, closing_payment_method FROM events WHERE message_type = 'CLOSING'"
    ).fetchone()
    assert row == (None, None)


def test_non_closing_events_never_populate_closing_columns(conn, vehicle_rules):
    job_id, job = seed_approved_job(conn, vehicle_rules)
    row = conn.execute(
        "SELECT closing_amount FROM events WHERE message_type = 'NEW'"
    ).fetchone()
    assert row == (None,)


def test_get_latest_closing_details_returns_none_when_never_closed(conn, vehicle_rules):
    job_id, job = seed_approved_job(conn, vehicle_rules)
    assert storage.get_latest_closing_details(conn, job_id) is None


def test_get_latest_closing_details_returns_most_recent(conn, vehicle_rules):
    job_id, job = seed_approved_job(conn, vehicle_rules)
    close_job(conn, job_id, job, closing_amount="100.00", closing_payment_method="Cash")
    close_job(conn, job_id, job, closing_amount="120.00", closing_payment_method="CC")  # corrected amount

    details = storage.get_latest_closing_details(conn, job_id)
    assert details["amount"] == "120.00"
    assert details["payment_method"] == "CC"


# -------------------------------------------------- Reconciliation state
def test_reconciliation_state_defaults_to_null(conn, vehicle_rules):
    job_id, job = seed_approved_job(conn, vehicle_rules)
    close_job(conn, job_id, job, closing_amount="120.00", closing_payment_method="CC")

    status, reconciliation_state = conn.execute(
        "SELECT status, reconciliation_state FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert status == "Closed"
    assert reconciliation_state is None  # "awaiting reconciliation" is the implicit default


def test_mark_job_settled_sets_reconciliation_state_only(conn, vehicle_rules):
    job_id, job = seed_approved_job(conn, vehicle_rules)
    close_job(conn, job_id, job, closing_amount="120.00", closing_payment_method="CC")

    status_before = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    storage.mark_job_settled(conn, job_id)

    status_after, reconciliation_state = conn.execute(
        "SELECT status, reconciliation_state FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert status_after == status_before == "Closed"  # status untouched by settlement
    assert reconciliation_state == "Settled"


def test_mark_job_settled_never_touches_events_table(conn, vehicle_rules):
    job_id, job = seed_approved_job(conn, vehicle_rules)
    close_job(conn, job_id, job, closing_amount="120.00")
    events_before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    storage.mark_job_settled(conn, job_id)

    events_after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert events_after == events_before


def test_save_audit_entry_backward_compatible_without_closing_kwargs(conn, vehicle_rules):
    """Every existing call site (app.py's Process/Status/Assignment flows,
    the whole rest of this test suite) calls save_audit_entry() without
    any closing_* argument -- confirms that still works unchanged."""
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    job_id = storage.save_audit_entry(conn, job, classify_result, final_action="Approve")
    assert job_id is not None
