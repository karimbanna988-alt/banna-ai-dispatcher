from datetime import datetime, timedelta

from dispatcher.rules.daily import parse_daily_rules

SAMPLE = """Today:
- Georgia open
- South Carolina open
- North Carolina open
- Florida closed
- Columbia technician available
- Savannah technician busy
- Charlotte technician off
- Do not accept BMW today
"""


def test_parses_open_and_closed_areas():
    rules = parse_daily_rules(SAMPLE)
    assert "Georgia" in rules.open_areas
    assert "South Carolina" in rules.open_areas
    assert "North Carolina" in rules.open_areas
    assert "Florida" in rules.closed_areas


def test_parses_technician_status():
    rules = parse_daily_rules(SAMPLE)
    assert rules.technician_status["Columbia"] == "available"
    assert rules.technician_status["Savannah"] == "busy"
    assert rules.technician_status["Charlotte"] == "off"


def test_parses_temporary_restrictions():
    rules = parse_daily_rules(SAMPLE)
    assert "BMW" in rules.temporary_restrictions


def test_clean_input_has_no_unparsed_lines():
    rules = parse_daily_rules(SAMPLE)
    assert rules.unparsed_lines == []
    assert rules.needs_confirmation is False


def test_unrecognized_line_is_flagged_not_guessed():
    rules = parse_daily_rules("Today:\n- Bob is feeling great about Tuesday\n")
    assert rules.needs_confirmation is True
    assert any("Bob" in line for line in rules.unparsed_lines)


def test_expiry():
    now = datetime(2026, 1, 1, 8, 0, 0)
    rules = parse_daily_rules(SAMPLE, shift_hours=12, now=now)
    assert rules.is_expired(now=now + timedelta(hours=11)) is False
    assert rules.is_expired(now=now + timedelta(hours=13)) is True


def test_extend_updates_expiry_and_flag():
    now = datetime(2026, 1, 1, 8, 0, 0)
    rules = parse_daily_rules(SAMPLE, shift_hours=12, now=now)
    new_expiry = now + timedelta(hours=24)
    rules.extend(new_expiry)
    assert rules.extended is True
    assert rules.expires_at == new_expiry
