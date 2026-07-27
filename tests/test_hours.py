from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from dispatcher.rules.hours import check_business_hours

NY = ZoneInfo("America/New_York")


def test_within_hours_midday():
    result = check_business_hours(datetime(2026, 1, 7, 12, 0, tzinfo=NY))
    assert result.within_hours is True


def test_before_opening_is_outside_hours():
    result = check_business_hours(datetime(2026, 1, 7, 7, 59, tzinfo=NY))
    assert result.within_hours is False


def test_at_opening_time_is_within_hours():
    result = check_business_hours(datetime(2026, 1, 7, 8, 0, tzinfo=NY))
    assert result.within_hours is True


def test_at_closing_time_is_outside_hours():
    result = check_business_hours(datetime(2026, 1, 7, 22, 0, tzinfo=NY))
    assert result.within_hours is False


def test_just_before_closing_is_within_hours():
    result = check_business_hours(datetime(2026, 1, 7, 21, 59, tzinfo=NY))
    assert result.within_hours is True


def test_late_night_is_outside_hours():
    result = check_business_hours(datetime(2026, 1, 7, 2, 0, tzinfo=NY))
    assert result.within_hours is False


def test_every_day_of_week_uses_the_same_hours():
    # "We operate every day" -- Saturday/Sunday get no special treatment here
    # (that's handled per-area via recurring_day_off, not business hours).
    saturday = check_business_hours(datetime(2026, 1, 10, 12, 0, tzinfo=NY))
    sunday = check_business_hours(datetime(2026, 1, 11, 12, 0, tzinfo=NY))
    assert saturday.within_hours is True
    assert sunday.within_hours is True


def test_converts_from_other_timezones_via_the_iana_database():
    """A UTC instant that is midday in New York should read as within
    hours, regardless of what offset it's expressed in -- proves the
    conversion goes through zoneinfo, not a hardcoded offset."""
    utc_instant = datetime(2026, 1, 7, 17, 0, tzinfo=timezone.utc)  # 12:00 EST
    result = check_business_hours(utc_instant)
    assert result.within_hours is True
    assert result.local_time.hour == 12


def test_naive_datetime_is_rejected_not_guessed():
    with pytest.raises(ValueError):
        check_business_hours(datetime(2026, 1, 7, 12, 0))


def test_defaults_to_real_current_time_when_not_given():
    result = check_business_hours()
    assert result.local_time.tzinfo is not None
