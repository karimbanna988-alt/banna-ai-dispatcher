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
        assert msg.startswith("LUXOR LOCKSMITH\nNEW SERVICE REQUEST")


def test_agn_exact_output(rules):
    """Exact-output regression test (confirmed 2026-07-27 canonical v2
    format): AGN has no customer name, has an extension, and no second
    phone -- Customer and Alternate Phone lines are both omitted."""
    job = extract_job(load("agn_clean.txt"), rules)
    assert format_technician_message(job) == (
        "LUXOR LOCKSMITH\n"
        "NEW SERVICE REQUEST\n"
        "\n"
        "Ticket: FAKE1\n"
        "Phone: 1-555-010-3000 ext 123\n"
        "\n"
        "Service Address:\n"
        "100 Fake St , Faketown, NC, 27500\n"
        "\n"
        "Service Requested:\n"
        "Car Lockout\n"
        "\n"
        "Vehicle:\n"
        "Honda Civic 2020\n"
        "\n"
        "Job Notes:\n"
        "Honda Civic 2020"
    )


def test_lucky_exact_output(rules):
    """Exact-output regression test: no name, no extension, no vehicle, no
    notes -- Customer/Alternate Phone/Vehicle/Job Notes all omitted."""
    job = extract_job(load("lucky_clean.txt"), rules)
    assert format_technician_message(job) == (
        "LUXOR LOCKSMITH\n"
        "NEW SERVICE REQUEST\n"
        "\n"
        "Ticket: FAKE2\n"
        "Phone: 5550104000\n"
        "\n"
        "Service Address:\n"
        "200 Fake Ln , Faketown, SC, 29000\n"
        "\n"
        "Service Requested:\n"
        "Home Lockout"
    )


def test_main_leads_exact_output(rules):
    """Exact-output regression test: has a customer name, no extension, no
    second phone (empty field). Job Notes intentionally duplicates the
    Vehicle line here -- notes are shown exactly as extracted, never
    trimmed or deduplicated (confirmed 2026-07-27)."""
    job = extract_job(load("main_leads_clean.txt"), rules)
    assert format_technician_message(job) == (
        "LUXOR LOCKSMITH\n"
        "NEW SERVICE REQUEST\n"
        "\n"
        "Ticket: FAKE-1234\n"
        "Customer: Jordan\n"
        "Phone: (555) 010-2000\n"
        "\n"
        "Service Address:\n"
        "Fakeville, GA 30301\n"
        "\n"
        "Service Requested:\n"
        "New Key Fob\n"
        "\n"
        "Vehicle:\n"
        "Toyota Prius 2025\n"
        "\n"
        "Job Notes:\n"
        "Toyota Prius 2025\n"
        "\n"
        "Toyota Prius 2025 customer says the fob is inside, called earlier and has been waiting"
    )


def test_missing_customer_name_omits_the_line_entirely(rules):
    """Design choice confirmed 2026-07-27: Customer (like Alternate Phone
    and Vehicle) is treated as optional and omitted when absent -- not
    shown as a "(not provided)" placeholder -- since AGN/Lucky commonly
    and legitimately have no name field at all."""
    job = extract_job(load("agn_clean.txt"), rules)
    msg = format_technician_message(job)
    assert "Customer" not in msg
    assert "(not provided)" not in msg


def test_missing_core_field_shows_a_placeholder_not_omitted(rules):
    """Core fields (Ticket/Phone/Service Address/Service Requested) show a
    placeholder rather than being silently omitted, since their absence is
    itself a real problem worth flagging to whoever reads the message."""
    job = extract_job(load("missing_fields.txt"), rules)
    msg = format_technician_message(job)
    assert "Phone: (not provided)" in msg
    assert "Service Address:\n(not provided)" in msg


def test_second_phone_included_as_alternate_phone(rules):
    job = extract_job(load("main_leads_with_second_phone.txt"), rules)
    msg = format_technician_message(job)
    assert "Alternate Phone: (555) 010-9000" in msg


def test_second_phone_line_omitted_when_absent(rules):
    job = extract_job(load("main_leads_clean.txt"), rules)
    msg = format_technician_message(job)
    assert "Alternate Phone" not in msg


def test_extension_kept_inline_with_phone(rules):
    """Extension isn't in the business owner's literal template spec, but
    dropping it would violate the standing "never remove the extension"
    rule -- confirmed 2026-07-27 to keep it inline with Phone."""
    job = extract_job(load("agn_clean.txt"), rules)
    msg = format_technician_message(job)
    assert "Phone: 1-555-010-3000 ext 123" in msg


def test_vehicle_line_omitted_when_absent(rules):
    job = extract_job(load("lucky_clean.txt"), rules)
    msg = format_technician_message(job)
    assert "Vehicle:" not in msg


def test_job_notes_omitted_when_absent(rules):
    job = extract_job(load("lucky_clean.txt"), rules)
    msg = format_technician_message(job)
    assert "Job Notes:" not in msg


def test_decision_status_never_changes_the_message_template(rules):
    """HANDLE_MANUALLY is an internal decision-layer concern -- the
    technician message format itself must never branch on job.status."""
    handled_manually_job = extract_job(load("missing_fields.txt"), rules)
    from dispatcher.models import ExtractionStatus

    assert handled_manually_job.status == ExtractionStatus.HANDLE_MANUALLY
    msg = format_technician_message(handled_manually_job)
    assert msg.startswith("LUXOR LOCKSMITH\nNEW SERVICE REQUEST")
