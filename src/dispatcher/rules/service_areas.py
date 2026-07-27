"""Permanent service-area lookup.

Loads rules/service_areas.yaml and matches a job's city/state against it.
Never guesses a dispatch/closing group: if either is missing on a matched
record, the lookup comes back "unconfirmed" rather than filled in. See
docs/phase1_scope.md, "Company profile architecture" and docs/architecture.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

SERVICE_AREAS_PATH = Path(__file__).resolve().parents[3] / "rules" / "service_areas.yaml"


WEEKDAY_NAMES = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)


@dataclass(frozen=True)
class ServiceAreaRecord:
    city: Optional[str]
    state: Optional[str]
    zip_codes: List[str] = field(default_factory=list)
    dispatch_group: Optional[str] = None
    closing_group: Optional[str] = None
    active: bool = True
    default_technician_availability: str = "unknown"
    company_restrictions: List[str] = field(default_factory=list)
    notes: str = ""
    # Recurring weekly day off (e.g. "Saturday"), not a daily message --
    # confirmed examples: Chicago (Saturday), Raleigh NC (Sunday). None
    # means no known recurring day off.
    recurring_day_off: Optional[str] = None
    # What a hit on recurring_day_off resolves to: "PASS" (a known, regular
    # closure -- treated like any other confirmed non-coverage day) or
    # "HANDLE_MANUALLY" (still ask a human every time). Configurable per
    # area rather than hardcoded.
    recurring_day_off_action: str = "PASS"


def is_recurring_day_off(record: ServiceAreaRecord, local_dt) -> bool:
    if not record.recurring_day_off:
        return False
    return local_dt.strftime("%A").lower() == record.recurring_day_off.strip().lower()


@dataclass
class ServiceAreaLookup:
    status: str  # "confirmed" | "unconfirmed" | "inactive" | "not_found"
    record: Optional[ServiceAreaRecord]
    dispatch_group: Optional[str]
    closing_group: Optional[str]
    reason: str


def load_service_areas(path: Optional[Path] = None) -> List[ServiceAreaRecord]:
    p = path or SERVICE_AREAS_PATH
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or []
    return [ServiceAreaRecord(**entry) for entry in raw]


def _norm(value: Optional[str]) -> Optional[str]:
    return value.strip().lower() if value else None


def find_service_area(
    city: Optional[str], state: Optional[str], areas: List[ServiceAreaRecord]
) -> ServiceAreaLookup:
    city_n, state_n = _norm(city), _norm(state)

    if not city_n and not state_n:
        return ServiceAreaLookup(
            status="not_found", record=None, dispatch_group=None, closing_group=None,
            reason="no city or state provided to match against the service-area mapping",
        )

    match: Optional[ServiceAreaRecord] = None

    # Prefer an exact city+state match when the record has a state.
    if city_n:
        for area in areas:
            if _norm(area.city) == city_n and area.state and state_n and _norm(area.state) == state_n:
                match = area
                break

    # Fall back to city-only match (covers areas where state is unconfirmed).
    if match is None and city_n:
        candidates = [a for a in areas if _norm(a.city) == city_n]
        if len(candidates) == 1:
            match = candidates[0]
        elif len(candidates) > 1:
            return ServiceAreaLookup(
                status="not_found", record=None, dispatch_group=None, closing_group=None,
                reason=f"'{city}' matches more than one service-area entry with different states "
                       f"-- state must be confirmed to disambiguate",
            )

    # Fall back to a whole-state entry (city is null on the record) when no city matched.
    if match is None and state_n:
        state_candidates = [a for a in areas if a.city is None and _norm(a.state) == state_n]
        if state_candidates:
            match = state_candidates[0]

    if match is None:
        return ServiceAreaLookup(
            status="not_found", record=None, dispatch_group=None, closing_group=None,
            reason=f"'{city or state}' was not found in the service-area mapping -- ask the dispatcher",
        )

    if not match.active:
        return ServiceAreaLookup(
            status="inactive", record=match, dispatch_group=None, closing_group=None,
            reason=f"{match.city or match.state} is marked inactive in the service-area mapping",
        )

    if not match.dispatch_group or not match.closing_group:
        return ServiceAreaLookup(
            status="unconfirmed", record=match, dispatch_group=None, closing_group=None,
            reason=f"{match.city or match.state} is a known working area, but its dispatch/closing "
                   f"WhatsApp group has not been confirmed yet -- ask the dispatcher",
        )

    return ServiceAreaLookup(
        status="confirmed", record=match, dispatch_group=match.dispatch_group,
        closing_group=match.closing_group, reason="matched a confirmed service-area entry",
    )
