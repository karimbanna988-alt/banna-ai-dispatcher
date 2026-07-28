"""Tests for the deterministic service-category classifier
(src/dispatcher/rules/service_categories.py). Grounded in the real
variety of service_type text observed across the existing test fixtures
(Home Lockout vs House Lockout, Car Key vs Car Keys) -- confirms the
classifier normalizes these correctly and never guesses when a car/house
qualifier is missing or contradictory."""

import pytest

from dispatcher.rules.service_categories import (
    CAR_KEYS,
    CAR_LOCKOUT,
    HOUSE_KEYS,
    HOUSE_LOCKOUT,
    LOCK_INSTALLATION,
    classify_service_categories,
    normalize_skill_label,
)


@pytest.mark.parametrize("text,expected", [
    ("Car Lockout", {CAR_LOCKOUT}),
    ("House Lockout", {HOUSE_LOCKOUT}),
    ("Home Lockout", {HOUSE_LOCKOUT}),  # "home" == "house" qualifier
    ("Business Lockout", {HOUSE_LOCKOUT}),
    ("Car Keys Made", {CAR_KEYS}),
    ("Car Key", {CAR_KEYS}),  # singular still recognized
    ("Car Ignition key", {CAR_KEYS}),
    ("House Keys", {HOUSE_KEYS}),
    ("Lock Installation", {LOCK_INSTALLATION}),
    ("Rekey house", {LOCK_INSTALLATION}),
])
def test_known_real_fixture_phrasings_classify_correctly(text, expected):
    assert classify_service_categories(text) == expected


@pytest.mark.parametrize("text", ["Lockout", "New Key Fob", "", None, "Service needed"])
def test_ambiguous_or_empty_text_never_guesses(text):
    """No car/house qualifier at all (or no recognizable category) ->
    empty set, never an invented category."""
    assert classify_service_categories(text) == set()


def test_both_qualifiers_present_is_ambiguous_not_guessed():
    """A hypothetical description mentioning both a car and a house
    context for a lockout must not silently pick one."""
    assert classify_service_categories("Car and house lockout") == set()


def test_normalize_skill_label():
    assert normalize_skill_label("Car Lockout") == "car_lockout"
    assert normalize_skill_label("car_lockout") == "car_lockout"
    assert normalize_skill_label("  House   Keys ") == "house_keys"
    assert normalize_skill_label(None) == ""
