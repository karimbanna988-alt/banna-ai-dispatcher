"""Which coverage area a real-world (city, state) belongs to.

This is a different question from the one rules/service_areas.yaml
answers. That file says whether a place is somewhere the business
works. This one says which coverage area the place belongs to -- the
name technicians are actually registered against, which is what
eligibility compares.

They came apart in practice. A job in Wilmington DE matched the
whole-state service-area entry and produced the label "DE", while every
Delaware technician is registered as "Delaware". Nobody was eligible,
and the board reported "no technician yet" for a job three people could
have taken.

TWO RULES THIS WILL NOT BEND:

  State is part of the key. A city name is not an identity. Wilmington
  DE is Delaware coverage; Wilmington NC is four hundred miles away and
  is not covered at all. Resolving on the city alone would route one to
  the other, which is worse than resolving nothing.

  Nothing is guessed. The mapping is an explicit list. A place that is
  not in it does not resolve, and the caller reports "no coverage area"
  for a human to answer. There is no fuzzy matching and no inference
  from a city to a nearby one.

Pure: no IO beyond loading the rules file once, no clock, no database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

COVERAGE_AREAS_PATH = Path(__file__).resolve().parents[3] / "rules" / "coverage_areas.yaml"

_CACHE: Optional[Dict[Tuple[Optional[str], Optional[str]], str]] = None

# Full state names -> the abbreviation the rules file uses. Deliberately
# the same short, explicit list style as service_areas.py: an
# unrecognized name is left alone and simply fails to match, which is
# the safe outcome.
_STATE_NAME_TO_ABBR = {
    "delaware": "DE", "alabama": "AL", "georgia": "GA",
    "south carolina": "SC", "north carolina": "NC", "new jersey": "NJ",
    "virginia": "VA", "illinois": "IL", "texas": "TX", "tennessee": "TN",
    "new york": "NY", "florida": "FL",
}


def _norm_city(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip().lower()
    return text or None


def _norm_state(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip().lower()
    if not text:
        return None
    return _STATE_NAME_TO_ABBR.get(text, text).upper()


def load_coverage_areas(path: Optional[Path] = None) -> List[dict]:
    with open(path or COVERAGE_AREAS_PATH, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or []


def _index(path: Optional[Path] = None) -> Dict[Tuple[Optional[str], Optional[str]], str]:
    global _CACHE
    if _CACHE is not None and path is None:
        return _CACHE
    built: Dict[Tuple[Optional[str], Optional[str]], str] = {}
    for entry in load_coverage_areas(path):
        area = (entry.get("area") or "").strip()
        if not area:
            continue
        built[(_norm_city(entry.get("city")), _norm_state(entry.get("state")))] = area
    if path is None:
        _CACHE = built
    return built


def resolve_coverage_area(city: Optional[str], state: Optional[str],
                          path: Optional[Path] = None) -> Optional[str]:
    """The coverage area for this (city, state), or None.

    None means "not in the mapping", which the caller must surface for a
    human rather than fill in. It never falls back to a city-only match:
    see this module's docstring for why that is the whole point.
    """
    city_n, state_n = _norm_city(city), _norm_state(state)
    index = _index(path)

    # Exact (city, state) first.
    if city_n and state_n and (city_n, state_n) in index:
        return index[(city_n, state_n)]

    # A city with no state can only match an entry that also has no
    # state. It must never borrow a state's answer: "Wilmington" with no
    # state is genuinely ambiguous between DE and NC, and guessing is
    # exactly the failure this module exists to prevent.
    if city_n and not state_n and (city_n, None) in index:
        return index[(city_n, None)]

    # Whole-state fallback, only when no city was given or the city is
    # unknown. A known state still tells us which coverage area a job
    # belongs to when the town is a small one we have not listed.
    if state_n and (None, state_n) in index:
        return index[(None, state_n)]

    return None
