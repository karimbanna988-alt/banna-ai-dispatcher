from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dispatcher.companies import agn, lucky
from dispatcher.models import ExtractedJob, ExtractionStatus, FieldValue, Source
from dispatcher.parser import extract_job, load_vehicle_rules
from dispatcher.rules import Verdict, decide, force_handle_manually
from dispatcher.rules.daily import parse_daily_rules
from dispatcher.rules.service_areas import ServiceAreaRecord, load_service_areas

FIXTURES = Path(__file__).parent / "fixtures"
NY = ZoneInfo("America/New_York")

# Wednesday, within 8am-10pm -- a plain "normal business hours, normal day" instant.
WITHIN_HOURS = datetime(2026, 1, 7, 12, 0, tzinfo=NY)
OUTSIDE_HOURS = datetime(2026, 1, 7, 2, 0, tzinfo=NY)
A_SATURDAY = datetime(2026, 1, 10, 12, 0, tzinfo=NY)
A_SUNDAY = datetime(2026, 1, 11, 12, 0, tzinfo=NY)


@pytest.fixture(scope="module")
def vehicle_rules():
    return load_vehicle_rules()


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def confirmed_area(city="Faketown", state="NC", **overrides):
    defaults = dict(
        city=city, state=state, dispatch_group="TestGroup", closing_group="TestClose",
        active=True, default_technician_availability="available",
    )
    defaults.update(overrides)
    return ServiceAreaRecord(**defaults)


def no_daily_rules():
    return None


def test_accept_when_everything_clean(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    decision = decide(job, agn.PROFILE, [confirmed_area()], business_now=WITHIN_HOURS)
    assert decision.verdict == Verdict.ACCEPT
    assert decision.dispatch_group == "TestGroup"
    assert decision.quo_reply_suggestion == "K"


def test_accept_with_no_daily_rules_at_all(vehicle_rules):
    """Areas are active by default and default technician availability is
    "available" -- a clean job should ACCEPT even with zero daily input,
    since there's no more daily open/closed messaging in real operation."""
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    decision = decide(job, agn.PROFILE, [confirmed_area()], daily_rules=None, business_now=WITHIN_HOURS)
    assert decision.verdict == Verdict.ACCEPT


def test_main_leads_never_suggests_k(vehicle_rules):
    from dispatcher.companies import main_leads

    job = extract_job(load("main_leads_clean.txt"), vehicle_rules)
    area = confirmed_area(city="Fakeville", state="GA")
    decision = decide(job, main_leads.PROFILE, [area], business_now=WITHIN_HOURS)
    assert decision.verdict == Verdict.ACCEPT
    assert decision.quo_reply_suggestion is None


def test_handle_manually_when_parser_already_forced_it(vehicle_rules):
    job = extract_job(load("missing_fields.txt"), vehicle_rules)
    assert job.status == ExtractionStatus.HANDLE_MANUALLY
    decision = decide(job, agn.PROFILE, [confirmed_area()], business_now=WITHIN_HOURS)
    assert decision.verdict == Verdict.HANDLE_MANUALLY


def test_handle_manually_outside_business_hours(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    decision = decide(job, agn.PROFILE, [confirmed_area()], business_now=OUTSIDE_HOURS)
    assert decision.verdict == Verdict.HANDLE_MANUALLY
    assert decision.reasons[0] == "Outside operating hours"


def test_handle_manually_when_area_not_found(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    decision = decide(job, agn.PROFILE, [], business_now=WITHIN_HOURS)
    assert decision.verdict == Verdict.HANDLE_MANUALLY
    assert "not found" in decision.reasons[0] or "not be found" in decision.reasons[0]


def test_handle_manually_when_area_group_unconfirmed(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    unconfirmed_area = ServiceAreaRecord(city="Faketown", state="NC")  # no dispatch/closing group
    decision = decide(job, agn.PROFILE, [unconfirmed_area], business_now=WITHIN_HOURS)
    assert decision.verdict == Verdict.HANDLE_MANUALLY
    assert "not been confirmed" in decision.reasons[0]


def test_pass_when_area_inactive(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    inactive_area = confirmed_area(active=False)
    decision = decide(job, agn.PROFILE, [inactive_area], business_now=WITHIN_HOURS)
    assert decision.verdict == Verdict.PASS


def test_pass_on_recurring_day_off(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    area = confirmed_area(recurring_day_off="Saturday")
    decision = decide(job, agn.PROFILE, [area], business_now=A_SATURDAY)
    assert decision.verdict == Verdict.PASS
    assert "recurring day off" in decision.reasons[0]


def test_recurring_day_off_can_be_configured_to_handle_manually(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    area = confirmed_area(recurring_day_off="Saturday", recurring_day_off_action="HANDLE_MANUALLY")
    decision = decide(job, agn.PROFILE, [area], business_now=A_SATURDAY)
    assert decision.verdict == Verdict.HANDLE_MANUALLY


def test_recurring_day_off_overridden_by_daily_open(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    area = confirmed_area(recurring_day_off="Saturday")
    daily = parse_daily_rules("Today:\n- Faketown open\n", now=A_SATURDAY.replace(tzinfo=None))
    decision = decide(job, agn.PROFILE, [area], daily, now=A_SATURDAY.replace(tzinfo=None), business_now=A_SATURDAY)
    assert decision.verdict == Verdict.ACCEPT


def test_real_chicago_recurring_day_off_is_saturday():
    areas = load_service_areas()
    chicago = next(a for a in areas if a.city == "Chicago")
    assert chicago.recurring_day_off == "Saturday"


def test_real_raleigh_recurring_day_off_is_sunday():
    areas = load_service_areas()
    raleigh = next(a for a in areas if a.city == "Raleigh")
    assert raleigh.state == "NC"
    assert raleigh.recurring_day_off == "Sunday"


def test_handle_manually_when_technician_availability_unknown(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    area = confirmed_area(default_technician_availability="unknown")
    decision = decide(job, agn.PROFILE, [area], business_now=WITHIN_HOURS)
    assert decision.verdict == Verdict.HANDLE_MANUALLY
    assert "unknown" in decision.reasons[0]


def test_pass_when_daily_area_marked_unavailable(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    naive_now = WITHIN_HOURS.replace(tzinfo=None)
    daily = parse_daily_rules("Today:\n- NC closed\n", now=naive_now)
    decision = decide(job, agn.PROFILE, [confirmed_area(state="NC")], daily, now=naive_now, business_now=WITHIN_HOURS)
    assert decision.verdict == Verdict.PASS
    assert "unavailable" in decision.reasons[0]


def test_pass_when_technician_busy_today(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    naive_now = WITHIN_HOURS.replace(tzinfo=None)
    daily = parse_daily_rules("Today:\n- TestGroup technician busy\n", now=naive_now)
    decision = decide(job, agn.PROFILE, [confirmed_area()], daily, now=naive_now, business_now=WITHIN_HOURS)
    assert decision.verdict == Verdict.PASS
    assert "busy" in decision.reasons[0]


def test_expired_daily_exceptions_are_ignored_not_blocking(vehicle_rules):
    """Daily exceptions are an optional overlay now -- once expired, they're
    simply ignored (falls back to the permanent/default model), they don't
    force HANDLE_MANUALLY the way missing daily input used to."""
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    naive_now = WITHIN_HOURS.replace(tzinfo=None)
    stale = parse_daily_rules(
        "Today:\n- TestGroup technician busy\n", shift_hours=1, now=naive_now - timedelta(hours=5),
    )
    decision = decide(job, agn.PROFILE, [confirmed_area()], stale, now=naive_now, business_now=WITHIN_HOURS)
    assert decision.verdict == Verdict.ACCEPT


def test_pass_when_vehicle_restricted(vehicle_rules):
    job = extract_job(load("restricted_vehicle.txt"), vehicle_rules)
    decision = decide(job, agn.PROFILE, [confirmed_area()], business_now=WITHIN_HOURS)
    assert decision.verdict == Verdict.PASS
    assert "Audi" in decision.reasons[0]


def test_handle_manually_when_vehicle_unclear(vehicle_rules):
    job = extract_job(load("unclear_vehicle.txt"), vehicle_rules)
    decision = decide(job, agn.PROFILE, [confirmed_area()], business_now=WITHIN_HOURS)
    assert decision.verdict == Verdict.HANDLE_MANUALLY


def test_pass_when_daily_temporary_restriction_matches_vehicle(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)  # Honda Civic 2020
    naive_now = WITHIN_HOURS.replace(tzinfo=None)
    daily = parse_daily_rules("Today:\n- Do not accept Honda today\n", now=naive_now)
    decision = decide(job, agn.PROFILE, [confirmed_area()], daily, now=naive_now, business_now=WITHIN_HOURS)
    assert decision.verdict == Verdict.PASS
    assert "Honda" in decision.reasons[0]


def test_real_service_area_mapping_forces_handle_manually_today(vehicle_rules):
    """Every current entry in rules/service_areas.yaml has no confirmed
    dispatch/closing group yet, so accepting against the real mapping must
    always come back HANDLE_MANUALLY until groups are confirmed."""
    job = extract_job(load("lucky_clean.txt"), vehicle_rules)  # Faketown, SC -- not even in the real mapping
    decision = decide(job, lucky.PROFILE, load_service_areas(), business_now=WITHIN_HOURS)
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
        second_phone=FieldValue(None, 0, "no 2nd phone"),
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

    decision = decide(synthetic, agn.PROFILE, [confirmed_area()], business_now=WITHIN_HOURS)
    assert decision.verdict == Verdict.NEEDS_REVIEW
    assert decision.confidence == 65


def test_force_handle_manually_escalates_an_accept(vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    decision = decide(job, agn.PROFILE, [confirmed_area()], business_now=WITHIN_HOURS)
    assert decision.verdict == Verdict.ACCEPT

    escalated = force_handle_manually(decision, "anomalous classification, review by hand")
    assert escalated.verdict == Verdict.HANDLE_MANUALLY
    assert escalated.reasons[0] == "anomalous classification, review by hand"
