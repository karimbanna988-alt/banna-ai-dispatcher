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
    return ServiceAreaRecord(city="Faketown", state="NC", dispatch_group="TestGroup", closing_group="TestClose")


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

    raw = load("agn_clean.txt")
    job = extract_job(raw, vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    now = datetime(2026, 1, 1, 8, 0, 0)
    daily = parse_daily_rules("Today:\n- Georgia open\n", now=now)
    decision = decide(job, agn.PROFILE, [confirmed_area()], daily, now=now)
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
    assert entry["luxor_message"].startswith("Luxor Locksmith")
    assert entry["final_action"] == "Approve"
    assert job_id is not None
