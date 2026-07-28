"""Permanent service-area lookup.

Loads rules/service_areas.yaml and matches a job's city/state against it.

2026-07-28: service areas hold ONLY geographic/business facts (city/state,
active flag, recurring day off, default technician availability). They
never held customer-facing routing information going forward -- dispatch
and closing group names are opaque destination strings that live on
individual technician records (see storage.py's technicians table), not
here. This module never reasons about those names; it only resolves
whether a job's location is a known, active service area. See
docs/phase1_scope.md, "Company profile architecture" and
docs/architecture.md section 10.
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
    """Geographic/business facts only -- no dispatch/closing group names.
    Those are opaque, technician-owned destination strings (see
    storage.py's technicians table), never area-level data."""

    city: Optional[str]
    state: Optional[str]
    zip_codes: List[str] = field(default_factory=list)
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
    """Status "unconfirmed" was retired 2026-07-28: it used to mean "known
    area, but no dispatch/closing group set" -- group names no longer live
    on areas at all, so a known, active area is "confirmed" as soon as
    it's found. Confirming eligibility to actually dispatch a job now
    happens one layer up, at technician assignment
    (storage.assign_technician)."""

    status: str  # "confirmed" | "inactive" | "not_found"
    record: Optional[ServiceAreaRecord]
    reason: str


def load_service_areas(path: Optional[Path] = None) -> List[ServiceAreaRecord]:
    p = path or SERVICE_AREAS_PATH
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or []
    return [ServiceAreaRecord(**entry) for entry in raw]


def _norm(value: Optional[str]) -> Optional[str]:
    return value.strip().lower() if value else None


# Full state names -> the two-letter abbreviation rules/service_areas.yaml
# itself always uses. Only the states that have actually come up in real
# business data so far -- never guess a state that isn't explicitly named
# here; an unrecognized full name is left as-is and will simply fail to
# match anything in find_service_area(), which is the correct, safe
# outcome (surfaced as "not found" rather than silently misresolved).
_STATE_NAME_TO_ABBR = {
    "south carolina": "SC", "north carolina": "NC", "new jersey": "NJ",
    "georgia": "GA", "washington": "WA", "virginia": "VA", "illinois": "IL",
    "delaware": "DE", "texas": "TX",
}

# Obvious, unambiguous typo corrections only -- deliberately a short,
# explicit allowlist, never a fuzzy/similarity matcher. Every key here
# corrects to a city name actually present in rules/service_areas.yaml.
_KNOWN_CITY_TYPO_CORRECTIONS = {
    "raliegh": "Raleigh", "charlottle": "Charlotte", "chichgo": "Chicago",
}


def resolve_area_text(raw_text: str, areas: List[ServiceAreaRecord]) -> ServiceAreaLookup:
    """Resolves a free-text place name -- "Columbia, South Carolina",
    "Raleigh, NC", "New Jersey", "raliegh" -- against the canonical
    service-area configuration, for contexts (like the technician roster
    import preview) that don't already have separately-parsed city/state
    fields the way an extracted job does. Reuses find_service_area()
    unchanged; this only normalizes free text into the (city, state)
    shape that function already expects. Never invents a match: an
    unrecognized place name (e.g. "Atlanta", which isn't in
    rules/service_areas.yaml at all) resolves to status "not_found",
    exactly like any other unconfirmed location."""
    text = (raw_text or "").strip()
    if not text:
        return ServiceAreaLookup(status="not_found", record=None, reason="no area text supplied")

    if "," in text:
        city_part, _, state_part = text.partition(",")
        city_part, state_part = city_part.strip(), state_part.strip()
    else:
        city_part, state_part = text, ""

    corrected_city = _KNOWN_CITY_TYPO_CORRECTIONS.get(city_part.lower(), city_part)

    # A bare, unqualified full state name (no comma, no city) -- e.g. the
    # whole input is "New Jersey" -- resolves as state-only, not a city.
    if not state_part and corrected_city.lower() in _STATE_NAME_TO_ABBR:
        return find_service_area(None, _STATE_NAME_TO_ABBR[corrected_city.lower()], areas)

    state_abbr = _STATE_NAME_TO_ABBR.get(state_part.lower(), state_part or None)
    return find_service_area(corrected_city or None, state_abbr, areas)


def find_service_area(
    city: Optional[str], state: Optional[str], areas: List[ServiceAreaRecord]
) -> ServiceAreaLookup:
    city_n, state_n = _norm(city), _norm(state)

    if not city_n and not state_n:
        return ServiceAreaLookup(
            status="not_found", record=None,
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
                status="not_found", record=None,
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
            status="not_found",
            record=None,
            reason=f"'{city or state}' was not found in the service-area mapping -- ask the dispatcher",
        )

    if not match.active:
        return ServiceAreaLookup(
            status="inactive", record=match,
            reason=f"{match.city or match.state} is marked inactive in the service-area mapping",
        )

    return ServiceAreaLookup(
        status="confirmed", record=match, reason="matched a known, active service-area entry",
    )
