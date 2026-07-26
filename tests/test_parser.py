from pathlib import Path

import pytest

from dispatcher.models import ExtractionStatus, Source
from dispatcher.parser import extract_job, load_vehicle_rules

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def rules():
    return load_vehicle_rules()


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- source detection -------------------------------------------------

def test_detects_main_leads(rules):
    job = extract_job(load("main_leads_clean.txt"), rules)
    assert job.source == Source.MAIN_LEADS
    assert job.source_confidence >= 90


def test_detects_agn(rules):
    job = extract_job(load("agn_clean.txt"), rules)
    assert job.source == Source.AGN
    assert job.source_confidence >= 90


def test_detects_lucky(rules):
    job = extract_job(load("lucky_clean.txt"), rules)
    assert job.source == Source.LUCKY
    assert job.source_confidence >= 90


def test_unknown_source_flagged(rules):
    job = extract_job(load("unknown_source.txt"), rules)
    assert job.source == Source.UNKNOWN
    assert job.status == ExtractionStatus.HANDLE_MANUALLY
    assert any("source company" in r for r in job.status_reasons)


# --- field extraction on clean jobs ------------------------------------

def test_main_leads_field_extraction(rules):
    job = extract_job(load("main_leads_clean.txt"), rules)
    assert job.ticket_number.value == "FAKE-1234"
    assert job.customer_name.value == "Jordan"
    assert job.phone.value == "(555) 010-2000"
    assert job.extension.value is None
    assert job.city.value == "Fakeville"
    assert job.state.value == "GA"
    assert job.zip_code.value == "30301"
    assert job.service_type.value == "New Key Fob"
    assert job.vehicle.value == "Toyota Prius 2025"
    assert job.notes.value is not None and "fob is inside" in job.notes.value
    assert job.status == ExtractionStatus.OK


def test_agn_field_extraction(rules):
    job = extract_job(load("agn_clean.txt"), rules)
    assert job.ticket_number.value == "FAKE1"
    assert job.customer_name.value is None
    assert job.phone.value == "1-555-010-3000"
    assert job.extension.value == "123"
    assert job.city.value == "Faketown"
    assert job.state.value == "NC"
    assert job.zip_code.value == "27500"
    assert job.service_type.value == "Car Lockout"
    assert job.vehicle.value == "Honda Civic 2020"
    assert job.status == ExtractionStatus.OK


def test_agn_with_bare_n_label_extracts_name(rules):
    """AGN doesn't always omit a name -- confirmed 2026-07-27 via a real
    AGN job using a bare 'N:' label. Must not be missed just because
    earlier samples never had one."""
    job = extract_job(load("agn_with_name.txt"), rules)
    assert job.source == Source.AGN
    assert job.ticket_number.value == "FAKE5"
    assert job.customer_name.value == "JOHN TESTMAN"
    assert job.phone.value == "1-555-010-7000"
    assert job.extension.value == "593"
    assert job.city.value == "Faketown"
    assert job.state.value == "VA"
    assert job.service_type.value == "House Change/Fix Locks"
    assert job.notes.value is not None and "IDENTIFY AS TEST BRAND VETERANS" in job.notes.value


def test_lucky_field_extraction(rules):
    job = extract_job(load("lucky_clean.txt"), rules)
    assert job.ticket_number.value == "FAKE2"
    assert job.phone.value == "5550104000"
    assert job.extension.value is None
    assert job.city.value == "Faketown"
    assert job.state.value == "SC"
    assert job.zip_code.value == "29000"
    assert job.service_type.value == "Home Lockout"
    assert job.status == ExtractionStatus.OK


# --- confidence + handle-manually triggers -----------------------------

def test_missing_required_fields_trigger_handle_manually(rules):
    job = extract_job(load("missing_fields.txt"), rules)
    assert job.phone.is_missing
    assert job.address.is_missing
    assert job.status == ExtractionStatus.HANDLE_MANUALLY
    reasons = " ".join(job.status_reasons)
    assert "phone" in reasons
    assert "address" in reasons


def test_conflicting_ticket_triggers_handle_manually(rules):
    job = extract_job(load("conflicting_ticket.txt"), rules)
    assert job.ticket_number.is_conflicting
    assert job.status == ExtractionStatus.HANDLE_MANUALLY
    assert any("ticket_number" in r for r in job.status_reasons)


def test_unclear_vehicle_triggers_handle_manually(rules):
    job = extract_job(load("unclear_vehicle.txt"), rules)
    assert job.vehicle_restriction_status == "unclear"
    assert job.status == ExtractionStatus.HANDLE_MANUALLY
    assert any("BMW" in r for r in job.status_reasons)


def test_restricted_vehicle_does_not_by_itself_force_handle_manually(rules):
    job = extract_job(load("restricted_vehicle.txt"), rules)
    assert job.vehicle_restriction_status == "restricted"
    assert "Audi" in job.vehicle_restriction_reason
    # All required fields are present and clean, and a *clear* restriction
    # (not an ambiguous one) is left for the business-rules layer (Phase 2+)
    # to turn into an actual pass decision -- extraction itself succeeded.
    assert job.status == ExtractionStatus.OK


def test_clean_jobs_never_report_missing_extension_as_a_problem(rules):
    job = extract_job(load("lucky_clean.txt"), rules)
    assert job.extension.is_missing
    assert job.extension.confidence == 100
    assert "not invented" in job.extension.reason


def test_second_phone_is_missing_when_field_is_empty(rules):
    job = extract_job(load("main_leads_clean.txt"), rules)
    assert job.second_phone.value is None


def test_second_phone_is_extracted_when_provided(rules):
    """Confirmed 2026-07-27 from a real Main Leads job: a genuine second
    contact number must never be silently dropped just because it wasn't
    part of the original required-field list."""
    job = extract_job(load("main_leads_with_second_phone.txt"), rules)
    assert job.second_phone.value == "(555) 010-9000"
