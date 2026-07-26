from pathlib import Path

import pytest

from dispatcher.formatter import format_technician_message
from dispatcher.parser import extract_job, load_vehicle_rules

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def rules():
    return load_vehicle_rules()


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_no_source_branding_in_technician_message(rules):
    for fixture in ("agn_clean.txt", "lucky_clean.txt", "main_leads_clean.txt"):
        job = extract_job(load(fixture), rules)
        msg = format_technician_message(job)
        assert "AGN" not in msg
        assert "LUCKY" not in msg
        assert "Ucallz" not in msg
        assert msg.startswith("Luxor Locksmith")


def test_preserves_ticket_phone_extension_and_service(rules):
    job = extract_job(load("agn_clean.txt"), rules)
    msg = format_technician_message(job)
    assert "FAKE1" in msg
    assert "1-555-010-3000" in msg
    assert "ext 123" in msg
    assert "Car Lockout" in msg
    assert "Honda Civic 2020" in msg


def test_missing_customer_name_shown_as_placeholder_not_invented(rules):
    job = extract_job(load("agn_clean.txt"), rules)
    msg = format_technician_message(job)
    assert "(not provided)" in msg


def test_main_leads_message_preserves_name_and_notes(rules):
    job = extract_job(load("main_leads_clean.txt"), rules)
    msg = format_technician_message(job)
    assert "Jordan" in msg
    assert "fob is inside" in msg
