from pathlib import Path

import pytest

from dispatcher.closing import ClosingInput, render_closing
from dispatcher.companies import agn, lucky, main_leads
from dispatcher.companies.base import SHARED_CLOSING_TEMPLATE, ClosingTemplate, CompanyProfile
from dispatcher.models import Source
from dispatcher.parser import extract_job, load_vehicle_rules

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def rules():
    return load_vehicle_rules()


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_agn_and_lucky_share_the_same_closing_template():
    assert agn.PROFILE.closing_template is SHARED_CLOSING_TEMPLATE
    assert lucky.PROFILE.closing_template is SHARED_CLOSING_TEMPLATE
    assert agn.PROFILE.closing_header_line != lucky.PROFILE.closing_header_line


def test_company_profile_can_override_the_shared_template():
    custom = ClosingTemplate(template="CUSTOM {ticket_number}", source_note="test override")
    profile = CompanyProfile(
        source=Source.AGN,
        requires_k=True,
        closing_supported=True,
        closing_template=custom,
        closing_header_line="whatever",
    )
    assert profile.closing_template is custom
    assert profile.closing_template is not SHARED_CLOSING_TEMPLATE


def test_lucky_closing_renders_with_parts(rules):
    job = extract_job(load("lucky_clean.txt"), rules)
    result = render_closing(job, ClosingInput(amount="$918", payment_method="CC", parts_amount="$86"))
    assert result.supported is True
    assert "Co: LUCKY LOCKSMITH LLC" in result.text
    assert "PDL: FAKE2" in result.text
    assert "Closed" in result.text
    assert "$918 CC" in result.text
    assert "$86 parts" in result.text


def test_lucky_closing_renders_without_parts(rules):
    job = extract_job(load("lucky_clean.txt"), rules)
    result = render_closing(job, ClosingInput(amount="$75", payment_method="CC"))
    assert result.supported is True
    assert "$75 CC" in result.text
    assert "parts" not in result.text


def test_agn_closing_uses_shared_template_with_its_own_header(rules):
    job = extract_job(load("agn_clean.txt"), rules)
    result = render_closing(job, ClosingInput(amount="$500", payment_method="CC"))
    assert result.supported is True
    assert "Ref: AGN Locksmith Services" in result.text
    assert "PDL: FAKE1" in result.text
    assert "$500 CC" in result.text


def test_main_leads_closing_is_not_applicable(rules):
    job = extract_job(load("main_leads_clean.txt"), rules)
    result = render_closing(job, ClosingInput(amount="$0", payment_method="CC"))
    assert result.supported is False
    assert "never require a closing" in result.reason
