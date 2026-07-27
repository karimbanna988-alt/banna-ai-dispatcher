"""Permanent business-hours check.

The business operates every day, 8:00 AM - 10:00 PM, America/New_York time.
Always convert via the IANA timezone -- never hardcode a fixed offset from
the operator's local time, since daylight-saving changes the conversion
(confirmed by the business owner 2026-07-27).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

BUSINESS_TIMEZONE = ZoneInfo("America/New_York")
OPENING_TIME = time(8, 0)
CLOSING_TIME = time(22, 0)


@dataclass
class HoursCheck:
    within_hours: bool
    local_time: datetime
    reason: str


def check_business_hours(now: Optional[datetime] = None) -> HoursCheck:
    """`now` must be timezone-aware if provided (e.g. for tests) -- a naive
    datetime is ambiguous about which timezone it represents and is
    rejected rather than guessed."""
    if now is None:
        now = datetime.now(BUSINESS_TIMEZONE)
    elif now.tzinfo is None:
        raise ValueError(
            "check_business_hours() requires a timezone-aware datetime -- "
            "a naive datetime doesn't say which timezone it's in"
        )

    local = now.astimezone(BUSINESS_TIMEZONE)
    within = OPENING_TIME <= local.time() < CLOSING_TIME
    if within:
        return HoursCheck(
            True, local,
            f"within operating hours ({OPENING_TIME.strftime('%H:%M')}-"
            f"{CLOSING_TIME.strftime('%H:%M')} {BUSINESS_TIMEZONE.key})",
        )
    return HoursCheck(
        False, local,
        f"outside operating hours ({OPENING_TIME.strftime('%H:%M')}-"
        f"{CLOSING_TIME.strftime('%H:%M')} {BUSINESS_TIMEZONE.key}; "
        f"local time is {local.strftime('%H:%M %Z')})",
    )
