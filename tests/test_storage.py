from pathlib import Path

import pytest

from dispatcher import storage
from dispatcher.companies import agn
from dispatcher.formatter import format_technician_message
from dispatcher.parser import extract_job, load_vehicle_rules
from dispatcher.rules import decide
from dispatcher.rules.daily import parse_daily_rules
from dispatcher.rules.service_areas import ServiceAreaRecord
from dispatcher.storage import MessageType

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def conn(tmp_path):
    return storage.connect(tmp_path / "test.db")


@pytest.fixture(scope="module")
def vehicle_rules():
    return load_vehicle_rules()


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def confirmed_area():
    return ServiceAreaRecord(
        city="Faketown", state="NC", dispatch_group="TestGroup", closing_group="TestClose",
        default_technician_availability="available",
    )


def test_first_message_for_a_ticket_is_new(conn, vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    result = storage.classify_incoming(conn, job)
    assert result.message_type == MessageType.NEW
    assert result.existing_job_id is None


def test_identical_repeat_is_duplicate(conn, vehicle_rules):
    raw = load("agn_clean.txt")
    job = extract_job(raw, vehicle_rules)
    result1 = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, result1, final_action="Approve")

    job_again = extract_job(raw, vehicle_rules)
    result2 = storage.classify_incoming(conn, job_again)
    assert result2.message_type == MessageType.DUPLICATE
    assert result2.existing_job_id == storage.classify_incoming(conn, job_again).existing_job_id


def test_conflicting_field_is_a_correction(conn, vehicle_rules):
    raw = load("agn_clean.txt")
    job = extract_job(raw, vehicle_rules)
    result1 = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, result1, final_action="Approve")

    corrected_raw = raw.replace("100 Fake St , Faketown, NC, 27500", "999 Other Rd , Faketown, NC, 27500")
    corrected_job = extract_job(corrected_raw, vehicle_rules)
    result2 = storage.classify_incoming(conn, corrected_job)
    assert result2.message_type == MessageType.CORRECTION
    assert "address" in result2.conflicting_fields


def test_closing_message_is_classified_as_closing(conn, vehicle_rules):
    raw = load("agn_clean.txt")
    job = extract_job(raw, vehicle_rules)
    result1 = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, result1, final_action="Approve")

    closing_raw = raw + "\nClosed\n$500 CC\n"
    closing_job = extract_job(closing_raw, vehicle_rules)
    result2 = storage.classify_incoming(conn, closing_job)
    assert result2.message_type == MessageType.CLOSING


def test_closing_with_no_prior_dispatch_is_anomalous_not_new(conn, vehicle_rules):
    """A ticket is always dispatched before it can be closed -- a
    closing-looking message with no prior record for its ticket must never
    be treated as a normal new job."""
    raw = load("agn_clean.txt") + "\nClosed\n$500 CC\n"
    job = extract_job(raw, vehicle_rules)
    result = storage.classify_incoming(conn, job)
    assert result.message_type == MessageType.CLOSING_WITHOUT_DISPATCH
    assert result.existing_job_id is None
    assert "handle manually" in result.reason.lower()


def test_cancellation_message_is_classified(conn, vehicle_rules):
    raw = load("agn_clean.txt")
    job = extract_job(raw, vehicle_rules)
    result1 = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, result1, final_action="Approve")

    cancel_raw = raw + "\nCustomer called to cancel\n"
    cancel_job = extract_job(cancel_raw, vehicle_rules)
    result2 = storage.classify_incoming(conn, cancel_job)
    assert result2.message_type == MessageType.CANCELLATION


def test_status_message_is_classified(conn, vehicle_rules):
    raw = load("agn_clean.txt")
    job = extract_job(raw, vehicle_rules)
    result1 = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, result1, final_action="Approve")

    status_job = extract_job("Going", vehicle_rules)
    status_job.ticket_number = job.ticket_number  # same ticket, freeform status text
    # A status update tied to this ticket is understood via which
    # company's thread it arrived in -- same source as the original job,
    # matching how app.py's Status Update tab already reuses the original
    # job's source rather than a blind ticket_number-only match (ticket
    # numbers are only unique WITHIN one source, not globally).
    status_job.source = job.source
    result2 = storage.classify_incoming(conn, status_job)
    assert result2.message_type == MessageType.STATUS


def test_no_ticket_is_always_new(conn, vehicle_rules):
    job = extract_job(load("unknown_source.txt"), vehicle_rules)
    result = storage.classify_incoming(conn, job)
    assert result.message_type == MessageType.NEW
    assert result.existing_job_id is None


def test_original_message_is_never_overwritten(conn, vehicle_rules):
    raw = load("agn_clean.txt")
    job = extract_job(raw, vehicle_rules)
    result1 = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, result1, final_action="Approve")

    corrected_raw = raw.replace("100 Fake St , Faketown, NC, 27500", "999 Other Rd , Faketown, NC, 27500")
    corrected_job = extract_job(corrected_raw, vehicle_rules)
    result2 = storage.classify_incoming(conn, corrected_job)
    storage.save_audit_entry(conn, corrected_job, result2, final_action="Approve")

    timeline = storage.get_audit_timeline(conn, job.ticket_number.value)
    assert len(timeline) == 2
    assert timeline[0]["raw_message"] == raw
    assert timeline[1]["raw_message"] == corrected_raw


def test_full_pipeline_writes_a_complete_audit_entry(conn, vehicle_rules):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    raw = load("agn_clean.txt")
    job = extract_job(raw, vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    now = datetime(2026, 1, 7, 12, 0, 0)  # Wednesday
    business_now = now.replace(tzinfo=ZoneInfo("America/New_York"))
    daily = parse_daily_rules("Today:\n- Georgia open\n", now=now)
    decision = decide(job, agn.PROFILE, [confirmed_area()], daily, now=now, business_now=business_now)
    luxor_message = format_technician_message(job)

    job_id = storage.save_audit_entry(
        conn, job, classify_result, decision=decision,
        suggested_quo_reply=decision.quo_reply_suggestion,
        luxor_message=luxor_message, final_action="Approve",
    )
    timeline = storage.get_audit_timeline(conn, job.ticket_number.value)
    assert len(timeline) == 1
    entry = timeline[0]
    assert entry["raw_message"] == raw
    assert entry["decision_verdict"] == "ACCEPT"
    assert entry["luxor_message"].startswith("LUXOR LOCKSMITH")
    assert entry["final_action"] == "Approve"
    assert job_id is not None


# ------------------------------------------------- Regression: crash fix ---
# Confirmed 2026-07-27/28: jobs.ticket_number was globally UNIQUE (not
# scoped per source), and save_audit_entry() trusted a caller-supplied
# existing_job_id that could go stale (e.g. the same loaded job finalized
# twice). Both crashed with sqlite3.IntegrityError: UNIQUE constraint
# failed: jobs.ticket_number, and the failure happened mid-INSERT, so the
# events row -- the audit trail of what the dispatcher actually did -- was
# lost too. These tests lock in that none of that can happen again.

def test_same_message_processed_twice_does_not_crash(conn, vehicle_rules):
    raw = load("agn_clean.txt")

    job1 = extract_job(raw, vehicle_rules)
    result1 = storage.classify_incoming(conn, job1)
    storage.save_audit_entry(conn, job1, result1, final_action="Approve")

    # Reprocess the exact same raw text from scratch, as if the same Quo
    # message were re-reviewed or re-delivered.
    job2 = extract_job(raw, vehicle_rules)
    result2 = storage.classify_incoming(conn, job2)
    assert result2.message_type == MessageType.DUPLICATE
    storage.save_audit_entry(conn, job2, result2, final_action="Approve")  # must not raise

    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE ticket_number = ?", (job1.ticket_number.value,)).fetchone()[0] == 1
    timeline = storage.get_audit_timeline(conn, job1.ticket_number.value)
    assert len(timeline) == 2  # both attempts preserved in the append-only log


def test_same_ticket_number_from_different_sources_does_not_collide(conn, vehicle_rules):
    agn_job = extract_job(load("agn_clean.txt"), vehicle_rules)
    lucky_job = extract_job(load("lucky_clean.txt"), vehicle_rules)
    # Force an artificial collision: two different companies' own,
    # independently-generated ticket numbering happening to coincide.
    shared_ticket = "SHARED123"
    agn_job.ticket_number.value = shared_ticket
    lucky_job.ticket_number.value = shared_ticket
    assert agn_job.source != lucky_job.source

    result_a = storage.classify_incoming(conn, agn_job)
    storage.save_audit_entry(conn, agn_job, result_a, final_action="Approve")

    result_l = storage.classify_incoming(conn, lucky_job)
    assert result_l.message_type == MessageType.NEW  # not mistaken for the AGN job
    storage.save_audit_entry(conn, lucky_job, result_l, final_action="Approve")  # must not raise

    rows = conn.execute(
        "SELECT source FROM jobs WHERE ticket_number = ? ORDER BY source", (shared_ticket,)
    ).fetchall()
    assert [r[0] for r in rows] == ["AGN", "Lucky"]


def test_repeated_handle_manually_on_the_same_loaded_job_does_not_crash(conn, vehicle_rules):
    """Reproduces the actual reported crash: the review screen doesn't
    clear itself after a save, and _finalize() never recomputes
    classify_result -- so clicking the same action button again (a
    double-click, or re-clicking after already handling it) reuses the
    same, now-stale ClassifyResult object."""
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    assert classify_result.existing_job_id is None  # first time, brand new

    storage.save_audit_entry(conn, job, classify_result, final_action="Handle Manually")
    # Re-finalize using the exact same (now stale) classify_result object,
    # simulating a second click without ever re-running classify_incoming.
    storage.save_audit_entry(conn, job, classify_result, final_action="Handle Manually")  # must not raise

    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE ticket_number = ?", (job.ticket_number.value,)).fetchone()[0] == 1


def test_audit_log_preserves_every_repeated_action(conn, vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)

    storage.save_audit_entry(conn, job, classify_result, final_action="Handle Manually")
    storage.save_audit_entry(conn, job, classify_result, final_action="Handle Manually")
    storage.save_audit_entry(conn, job, classify_result, final_action="Approve")

    timeline = storage.get_audit_timeline(conn, job.ticket_number.value)
    assert [e["final_action"] for e in timeline] == ["Handle Manually", "Handle Manually", "Approve"]
    # jobs stays a single, current-state row throughout
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE ticket_number = ?", (job.ticket_number.value,)).fetchone()[0] == 1
