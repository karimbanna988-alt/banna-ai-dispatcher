"""A minimal clock seam -- so tests can control "now" explicitly instead
of depending on wall-clock time (which is how earlier test fixtures
became stale: a hardcoded "recent" timestamp eventually aged out of a
live-queue boundary window as real sessions ran days/weeks later).

No third-party time library. `SystemClock` is the only clock used in
production (the default everywhere `_now()` is called without an
explicit clock); `FixedClock` is for tests that need a specific,
non-advancing instant.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """Real, timezone-aware UTC wall-clock time. The only clock used in
    production code."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    """Returns the same instant every call, unless explicitly advanced.
    Tests use this to make "age" / "is this live" calculations
    deterministic instead of racing real time."""

    def __init__(self, at: Optional[datetime] = None) -> None:
        self._at = at or datetime(2026, 1, 1, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._at

    def advance(self, delta: timedelta) -> None:
        self._at = self._at + delta

    def set(self, at: datetime) -> None:
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        self._at = at


def now_iso(clock: Optional[Clock] = None) -> str:
    """The one place an ISO-8601 UTC timestamp string gets formatted for
    storage -- matches the exact format _now() has always produced
    (e.g. "2026-01-01T00:00:00+00:00"), so switching a call site to use
    this changes nothing about what's stored, only where "now" comes
    from."""
    return (clock or SystemClock()).now().isoformat()
