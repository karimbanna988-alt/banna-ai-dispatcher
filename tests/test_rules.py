from datetime import datetime, timedelta
from pathlib import Path

import pytest

from dispatcher.companies import agn, lucky
from dispatcher.models import ExtractedJob, ExtractionStatus, FieldValue, Source
from dispatcher.parser import extract_job, load_vehicle_rules
from dispatcher.rules import Verdict, decide
from dispatcher.rules.daily import parse_daily_rules
from dispatcher.rules.service_areas import ServiceAreaRecord

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def vehicle_rules():
    return load_vehicle_rules()


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def confirmed_area(city="Faketown", state="NC", **overrides):
    defaults = dict(city=city, state=state, dispatch_group="TestGroup", closing_group="TestClose", active=True)
    defaults.update(overrides)
    return ServiceAreaRecord(**defaults)


def clean_daily_rules(now):
    return parse_daily_rules("Today:\n- Georgia open\n", now=now)


def test_accept_when_everything_clean(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    now = datetime(2026, 1, 1, 8, 0, 0)
    decision = decide(job, agn.PROFILE, [confirmed_area()], clean_daily_rules(now), now=now)
    assert decision.verdict == Verdict.ACCEPT
    assert decision.dispatch_group == "TestGroup"
    assert decision.quo_reply_suggestion == "K"


def test_main_leads_never_suggests_k(vehicle_rules):
    from dispatcher.companies import main_leads

    job = extract_job(load("main_leads_clean.txt"), vehicle_rules)
    now = datetime(2026, 1, 1, 8, 0, 0)
    area = confirmed_area(city="Fakeville", state="GA")
    decision = decide(job, main_leads.PROFILE, [area], clean_daily_rules(now), now=now)
    assert decision.verdict == Verdict.ACCEPT
    assert decision.quo_reply_suggestion is None


def test_handle_manually_when_parser_already_forced_it(vehicle_rules):
    job = extract_job(load("missing_fields.txt"), vehicle_rules)
    assert job.status == ExtractionStatus.HANDLE_MANUALLY
    now = datetime(2026, 1, 1, 8, 0, 0)
    decision = decide(job, agn.PROFILE, [confirmed_area()], clean_daily_rules(now), now=now)
    assert decision.verdict == Verdict.HANDLE_MANUALLY


def test_handle_manually_when_area_not_found(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    now = datetime(2026, 1, 1, 8, 0, 0)
    decision = decide(job, agn.PROFILE, [], clean_daily_rules(now), now=now)
    assert decision.verdict == Verdict.HANDLE_MANUALLY
    assert "not found" in decision.reasons[0] or "not be found" in decision.reasons[0]


def test_handle_manually_when_area_group_unconfirmed(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    now = datetime(2026, 1, 1, 8, 0, 0)
    unconfirmed_area = ServiceAreaRecord(city="Faketown", state="NC")  # no dispatch/closing group
    decision = decide(job, agn.PROFILE, [unconfirmed_area], clean_daily_rules(now), now=now)
    assert decision.verdict == Verdict.HANDLE_MANUALLY
    assert "not been confirmed" in decision.reasons[0]


def test_pass_when_area_inactive(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    now = datetime(2026, 1, 1, 8, 0, 0)
    inactive_area = confirmed_area(active=False)
    decision = decide(job, agn.PROFILE, [inactive_area], clean_daily_rules(now), now=now)
    assert decision.verdict == Verdict.PASS


def test_handle_manually_when_no_daily_rules_provided(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    decision = decide(job, agn.PROFILE, [confirmed_area()], daily_rules=None)
    assert decision.verdict == Verdict.HANDLE_MANUALLY
    assert "no daily rules" in decision.reasons[0]


def test_handle_manually_when_daily_rules_expired(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    now = datetime(2026, 1, 1, 8, 0, 0)
    stale_rules = parse_daily_rules("Today:\n- Georgia open\n", shift_hours=1, now=now - timedelta(hours=5))
    decision = decide(job, agn.PROFILE, [confirmed_area()], stale_rules, now=now)
    assert decision.verdict == Verdict.HANDLE_MANUALLY
    assert "expired" in decision.reasons[0]


def test_pass_when_daily_area_closed(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    now = datetime(2026, 1, 1, 8, 0, 0)
    daily = parse_daily_rules("Today:\n- NC closed\n", now=now)
    decision = decide(job, agn.PROFILE, [confirmed_area(state="NC")], daily, now=now)
    assert decision.verdict == Verdict.PASS
    assert "closed" in decision.reasons[0]


def test_pass_when_technician_busy(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    now = datetime(2026, 1, 1, 8, 0, 0)
    daily = parse_daily_rules("Today:\n- TestGroup technician busy\n", now=now)
    decision = decide(job, agn.PROFILE, [confirmed_area()], daily, now=now)
    assert decision.verdict == Verdict.PASS
    assert "busy" in decision.reasons[0]


def test_pass_when_vehicle_restricted(vehicle_rules):
    job = extract_job(load("restricted_vehicle.txt"), vehicle_rules)
    now = datetime(2026, 1, 1, 8, 0, 0)
    decision = decide(job, agn.PROFILE, [confirmed_area()], clean_daily_rules(now), now=now)
    assert decision.verdict == Verdict.PASS
    assert "Audi" in decision.reasons[0]


def test_handle_manually_when_vehicle_unclear(vehicle_rules):
    job = extract_job(load("unclear_vehicle.txt"), vehicle_rules)
    now = datetime(2026, 1, 1, 8, 0, 0)
    decision = decide(job, agn.PROFILE, [confirmed_area()], clean_daily_rules(now), now=now)
    assert decision.verdict == Verdict.HANDLE_MANUALLY


def test_pass_when_daily_temporary_restriction_matches_vehicle(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)  # Honda Civic 2020
    now = datetime(2026, 1, 1, 8, 0, 0)
    daily = parse_daily_rules("Today:\n- Do not accept Honda today\n", now=now)
    decision = decide(job, agn.PROFILE, [confirmed_area()], daily, now=now)
    assert decision.verdict == Verdict.PASS
    assert "Honda" in decision.reasons[0]


def test_real_service_area_mapping_forces_handle_manually_today(vehicle_rules):
    """Every current entry in rules/service_areas.yaml has no confirmed
    dispatch/closing group yet, so accepting against the real mapping must
    always come back HANDLE_MANUALLY until groups are confirmed."""
    from dispatcher.rules.service_areas import load_service_areas

    job = extract_job(load("lucky_clean.txt"), vehicle_rules)  # Faketown, SC -- not even in the real mapping
    now = datetime(2026, 1, 1, 8, 0, 0)
    decision = decide(job, lucky.PROFILE, load_service_areas(), clean_daily_rules(now), now=now)
    assert decision.verdict == Verdict.HANDLE_MANUALLY


def test_needs_review_when_confidence_below_threshold():
    synthetic = ExtractedJob(
        raw_text="synthetic",
        source=Source.AGN,
        source_confidence=95,
        source_reason="test",
        ticket_number=FieldValue("T1", 65, "test"),
        customer_name=FieldValue(None, 0, "n/a"),
        phone=FieldValue("555-0000", 65, "test"),
        extension=FieldValue(None, 100, "not invented"),
        address=FieldValue("1 Test St, Faketown, NC 27500", 65, "test"),
        city=FieldValue("Faketown", 90, "test"),
        state=FieldValue("NC", 90, "test"),
        zip_code=FieldValue("27500", 85, "test"),
        service_type=FieldValue("Lockout", 65, "test"),
        vehicle=FieldValue(None, 0, "no vehicle"),
        notes=FieldValue(None, 0, "no notes"),
    )
    synthetic.vehicle_restriction_status = "not_applicable"
    synthetic.vehicle_restriction_reason = "no vehicle info to check"
    synthetic.overall_confidence = 65
    synthetic.status = ExtractionStatus.OK
    synthetic.status_reasons = []

    now = datetime(2026, 1, 1, 8, 0, 0)
    decision = decide(synthetic, agn.PROFILE, [confirmed_area()], clean_daily_rules(now), now=now)
    assert decision.verdict == Verdict.NEEDS_REVIEW
    assert decision.confidence == 65
