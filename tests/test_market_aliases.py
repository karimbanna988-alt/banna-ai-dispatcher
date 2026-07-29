"""Tests for the Main Leads market-label -> canonical-area resolver.
Synthetic labels/addresses only -- no real customer data."""

from dispatcher.rules.market_aliases import market_alias_area, resolve_market_and_area


def test_atlantic_city_maps_to_nj_never_atlanta():
    assert market_alias_area("Atlantic City Locksmith Pro") == "NJ"
    assert market_alias_area("Atlantic City Locksmith Pro") != "Atlanta"


def test_virginia_beach_label_maps_to_virginia_beach():
    assert market_alias_area("Virginia Beach Locksmith") == "Virginia Beach"


def test_arlington_label_maps_to_arlington():
    assert market_alias_area("Arlington Locksmith Pro") == "Arlington"


def test_savannah_label_maps_to_savannah():
    assert market_alias_area("Locksmith Pros Savannah") == "Savannah"


def test_vancouver_label_maps_to_vancouver():
    assert market_alias_area("Vancouver Locksmith Pro") == "Vancouver"


def test_generic_locksmith_pros_label_alone_never_resolves():
    """The generic label must never determine area by itself -- address
    resolution is required."""
    assert market_alias_area("Locksmith Pros") is None


def test_unrecognized_label_resolves_to_none_not_a_guess():
    assert market_alias_area("Some New Fabricated Market Label") is None


def test_case_and_whitespace_insensitive_exact_match():
    assert market_alias_area("  ATLANTIC CITY locksmith PRO  ") == "NJ"


# ------------------------------------------------- resolve_market_and_area
def test_address_area_wins_when_present_and_alias_agrees():
    res = resolve_market_and_area(address_area="NJ", market_label="Atlantic City Locksmith Pro")
    assert res.status == "resolved"
    assert res.canonical_area == "NJ"


def test_address_and_alias_conflict_is_needs_review_never_invented():
    res = resolve_market_and_area(address_area="Chicago", market_label="Atlantic City Locksmith Pro")
    assert res.status == "needs_review"
    assert res.canonical_area is None


def test_address_only_resolves_without_any_market_label():
    res = resolve_market_and_area(address_area="Savannah", market_label=None)
    assert res.status == "resolved"
    assert res.canonical_area == "Savannah"


def test_generic_label_with_no_address_is_needs_review():
    res = resolve_market_and_area(address_area=None, market_label="Locksmith Pros")
    assert res.status == "needs_review"
    assert res.canonical_area is None


def test_confirmed_alias_with_no_address_resolves_as_fallback():
    res = resolve_market_and_area(address_area=None, market_label="Vancouver Locksmith Pro")
    assert res.status == "resolved"
    assert res.canonical_area == "Vancouver"


def test_neither_address_nor_alias_is_needs_review():
    res = resolve_market_and_area(address_area=None, market_label=None)
    assert res.status == "needs_review"
