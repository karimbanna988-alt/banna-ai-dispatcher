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


def test_year_from_two_digits_pivot():
    from dispatcher.parser import year_from_two_digits

    assert year_from_two_digits("12") == 2012
    assert year_from_two_digits("69") == 2069
    assert year_from_two_digits("70") == 1970
    assert year_from_two_digits("99") == 1999
    assert year_from_two_digits("00") == 2000


def test_two_digit_year_shorthand_still_triggers_restriction_when_over_cap(rules):
    """A 2-digit year shouldn't just be recognized -- it must actually be
    used for restriction math, e.g. a BMW newer than the 2014 cap."""
    from dispatcher.parser import check_vehicle_restriction

    status, reason = check_vehicle_restriction("bmw x5 16", rules)
    assert status == "restricted"
    assert "2016" in reason


def test_two_digit_vehicle_year_shorthand_is_recognized(rules):
    """Confirmed 2026-07-27 from a real AGN job: "honda accord 12" means a
    2012 Accord. Without this, the missing year wrongly forced
    HANDLE_MANUALLY even though a human would trivially read the year."""
    job = extract_job(load("agn_two_digit_year_vehicle.txt"), rules)
    assert job.vehicle.value == "honda accord 12"
    assert job.vehicle.confidence == 78
    assert "2012" in job.vehicle.reason
    assert job.vehicle_restriction_status == "allowed"
    assert job.status == ExtractionStatus.OK


def test_bare_trailing_extension_is_split_from_phone(rules):
    """Confirmed 2026-07-27 from a real Lucky job: "Ph: 5551234567 133" has
    no (Job Ext: N) wrapper -- the phone and extension must still be split,
    not glued into one unusable dial string."""
    job = extract_job(load("lucky_bare_extension_and_double_notes.txt"), rules)
    assert job.phone.value == "5551234567"
    assert job.extension.value == "133"


def test_normally_spaced_phone_number_is_not_mistaken_for_an_extension(rules):
    from dispatcher.parser import extract_phone_and_extension

    phone, ext = extract_phone_and_extension("Ph: (571) 431-9558")
    assert phone.value == "(571) 431-9558"
    assert ext.value is None


def test_last_notes_label_wins_when_repeated(rules):
    """Confirmed 2026-07-27 from the same real Lucky job: Quo can repeat an
    empty "Notes:" label before the one that actually has content -- the
    redundant empty label must not leak into the extracted value."""
    job = extract_job(load("lucky_bare_extension_and_double_notes.txt"), rules)
    assert job.notes.value == "CX IS AT BEST BUY"


def test_main_leads_notes_do_not_swallow_structured_fields_without_a_blank_line(rules):
    """Confirmed 2026-07-27 from a real Main Leads job: there is no blank
    line between the structured fields and the freeform vehicle/notes tail
    at all -- the old block-splitting heuristic (which only worked when a
    blank line happened to be present) wrongly swallowed the entire
    message, including the ticket/name/phone/address, into "notes"."""
    job = extract_job(load("main_leads_no_blank_line_before_notes.txt"), rules)
    assert job.notes.value == (
        "RV lock-out latch is not working// 2006 Forest River//"
        "  call back  555 010 6000 // service as soon as possible"
    )
    assert "FAKE8-TEST" not in job.notes.value
    assert "Testerson" not in job.notes.value
    assert "Faketown" not in job.notes.value


def test_agn_trailing_content_with_no_notes_label_is_still_captured(rules):
    """Confirmed 2026-07-27 from a real AGN job ending in a bare "K?" with
    no "Notes:" label anywhere -- previously silently dropped entirely
    (not in any field, not anywhere). Must now be captured, anchored on
    the last recognized field (here, "Occu:")."""
    job = extract_job(load("agn_no_notes_label_trailing_content.txt"), rules)
    assert job.notes.value == "K?"
    assert "captured verbatim after the last recognized field" in job.notes.reason


def test_lucky_trailing_content_with_no_notes_label_is_still_captured(rules):
    job = extract_job(load("lucky_no_notes_label_trailing_content.txt"), rules)
    assert job.notes.value == "please call before arriving"


def test_agn_notes_label_still_takes_priority_when_present(rules):
    """Regression guard: the new no-label fallback must not kick in (or
    otherwise change behavior) when a real "Notes:" label exists."""
    job = extract_job(load("agn_clean.txt"), rules)
    assert job.notes.value == "Honda Civic 2020"
    assert "captured verbatim after the last recognized field" in job.notes.reason


def test_two_distinct_notes_sections_both_preserved(rules):
    """Confirmed 2026-07-27 from a real Lucky job: unlike the earlier
    empty-label-then-content pattern, here BOTH "Notes:" occurrences carry
    real, different content ("2018 ford f250" before the first, "npg, pls
    call" after the second). Collapsing to only the last label would have
    dropped the vehicle info -- it must be preserved."""
    job = extract_job(load("lucky_two_distinct_notes_sections.txt"), rules)
    assert "2018 ford f250" in job.notes.value
    assert "npg, pls call" in job.notes.value


def test_unrecognized_field_before_empty_notes_label_is_preserved(rules):
    """Confirmed 2026-07-27 from a real AGN job: an "Appt:" line (a
    scheduled appointment time -- operationally critical) appeared between
    "Occu:" and an empty "Notes:" label. Previously silently dropped
    entirely: not in any field, not anywhere. Must now be preserved."""
    job = extract_job(load("agn_appt_field_empty_notes.txt"), rules)
    assert job.notes.value is not None
    assert "Appt: 07/26 at 08:00 AM UTC" in job.notes.value


def test_second_desc_line_is_preserved_not_used_as_notes_anchor(rules):
    """Confirmed 2026-07-27 from a real Lucky job: a second "Desc:" line
    ("Desc: Car Key") appeared after "Notes:", following a first "Desc: Car
    Ignition key" used for service_type. Because "Desc:" was previously
    treated as a recognized-field anchor, notes-extraction jumped past this
    second Desc: line entirely -- it wasn't in service_type (which only
    ever takes the first Desc: match) and wasn't in notes either, silently
    dropped. Must now be preserved verbatim in notes, even though the
    literal label text "Desc:" ends up inside it."""
    job = extract_job(load("lucky_second_desc_line_preserved.txt"), rules)
    assert job.service_type.value == "Car Ignition key"
    assert job.notes.value is not None
    assert "Desc: Car Key" in job.notes.value
    assert "2005 Lincoln Navigation" in job.notes.value
