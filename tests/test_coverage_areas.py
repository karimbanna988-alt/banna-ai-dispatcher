"""(city, state) -> coverage area, never city alone.

The bug: a job in Wilmington DE matched the whole-state service-area
entry, produced the label "DE", and found nobody -- because every
Delaware technician is registered as "Delaware". The board reported
"no technician yet" for a job three people could have taken.

The trap the fix must not fall into: resolving on the city alone would
have "fixed" Wilmington DE and simultaneously routed Wilmington NC --
four hundred miles away, not covered at all -- to a Delaware
technician. Silently sending someone to the wrong state is a worse
failure than naming nobody, so these tests pin the state-awareness as
hard as they pin the resolution.
"""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from dispatcher.rules.coverage_areas import resolve_coverage_area  # noqa: E402


# ---------------------------------------------------------------- the ask --

@pytest.mark.parametrize("city,state,expected", [
    ("Wilmington", "DE", "Delaware"),
    ("Birmingham", "AL", "Alabama"),
    ("Evans", "GA", "Augusta"),
    ("Aiken", "SC", "Augusta"),
    ("North Augusta", "SC", "Augusta"),
    ("Martinez", "GA", "Augusta"),
])
def test_known_places_resolve_to_their_coverage_area(city, state, expected):
    assert resolve_coverage_area(city, state) == expected


def test_wilmington_nc_is_not_wilmington_de():
    """The whole reason state is part of the key."""
    assert resolve_coverage_area("Wilmington", "DE") == "Delaware"
    assert resolve_coverage_area("Wilmington", "NC") != "Delaware"
    assert resolve_coverage_area("Wilmington", "NC") is None


def test_a_city_with_no_state_never_borrows_a_states_answer():
    """"Wilmington" alone is genuinely ambiguous. Guessing DE because it
    is the one we happen to list is the failure, not the fix."""
    assert resolve_coverage_area("Wilmington", None) is None


# ------------------------------------------------- the Augusta state line --

def test_the_augusta_market_crosses_the_state_line():
    """Aiken and North Augusta are SC, Evans and Martinez are GA, and all
    four are Augusta coverage. A state-only rule would split them."""
    georgia = {resolve_coverage_area(c, "GA") for c in ("Augusta", "Evans", "Martinez")}
    carolina = {resolve_coverage_area(c, "SC") for c in ("Aiken", "North Augusta")}
    assert georgia == {"Augusta"}
    assert carolina == {"Augusta"}


def test_sc_towns_in_the_augusta_market_are_not_columbia_or_charleston():
    for city in ("Aiken", "North Augusta"):
        assert resolve_coverage_area(city, "SC") == "Augusta"


# ------------------------------------------------------------- robustness --

@pytest.mark.parametrize("state", ["DE", "de", "De", "Delaware", "delaware", " DE "])
def test_state_is_accepted_in_the_forms_a_message_actually_uses(state):
    assert resolve_coverage_area("Wilmington", state) == "Delaware"


@pytest.mark.parametrize("city", ["north augusta", "NORTH AUGUSTA", "  North   Augusta  "])
def test_city_spacing_and_case_do_not_matter(city):
    assert resolve_coverage_area(city, "SC") == "Augusta"


def test_an_unknown_place_resolves_to_nothing_rather_than_a_guess():
    assert resolve_coverage_area("Atlanta", "GA") is None
    assert resolve_coverage_area("Nowhere", "ZZ") is None
    assert resolve_coverage_area(None, None) is None
    assert resolve_coverage_area("", "") is None


def test_a_known_state_resolves_even_when_the_town_is_not_listed():
    """A small DE town we have not enumerated is still Delaware work."""
    assert resolve_coverage_area("Smyrna", "DE") == "Delaware"
    assert resolve_coverage_area("Hoover", "AL") == "Alabama"


def test_an_unlisted_town_in_an_unlisted_state_stays_unresolved():
    assert resolve_coverage_area("Smyrna", "GA") is None


# -------------------------------------------- it must name real coverage --

def test_every_area_in_the_map_is_one_a_technician_is_registered_against():
    """A coverage area nobody is registered against can never produce an
    eligible technician, so it would reintroduce the exact silent
    failure this module was written to remove."""
    from dispatcher.rules.coverage_areas import load_coverage_areas
    areas = {e["area"] for e in load_coverage_areas()}
    assert areas == {"Delaware", "Alabama", "Augusta"}, (
        "if you add a coverage area here, register a technician against it too")
