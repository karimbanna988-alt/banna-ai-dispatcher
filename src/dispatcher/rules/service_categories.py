"""Deterministic service-category normalization for technician skill
matching.

Why this exists: storage.assign_technician()'s existing skill check is a
bare substring test (`skill.lower() in service_type.lower()`). That's
reliable only when a technician's skill string and a job's extracted
service_type text happen to use the exact same words. Real extracted
service_type text varies more than that -- confirmed by surveying every
test fixture's `Desc:` value: "Home Lockout" (not "House Lockout"),
"Car Key" (singular, not "Car Keys"), "Business Lockout", "Car Ignition
key". A technician configured with skill "house lockout" would never
match a job whose service_type says "Home Lockout" under plain substring
matching, even though they mean the same thing.

This module doesn't replace that substring check -- storage.py's
_skill_matches() tries this classifier first (for the five known
categories below) and falls back to the original substring test for any
other, uncontrolled skill string, so existing/future free-text skills
keep working exactly as before.

Never guesses: text with no car/house qualifier (e.g. bare "Lockout", or
"New Key Fob" with no house/car context) classifies to an empty set
rather than assuming either -- callers already treat "no confirmed skill
match" as a reason to fall back to Handle Manually, never as license to
invent a match.
"""

from __future__ import annotations

import re

CAR_LOCKOUT = "car_lockout"
HOUSE_LOCKOUT = "house_lockout"
CAR_KEYS = "car_keys"
HOUSE_KEYS = "house_keys"
LOCK_INSTALLATION = "lock_installation"

KNOWN_SERVICE_CATEGORIES = (CAR_LOCKOUT, HOUSE_LOCKOUT, CAR_KEYS, HOUSE_KEYS, LOCK_INSTALLATION)

_HOUSE_QUALIFIER_RE = re.compile(r"\b(house|home|business|office|commercial)\b", re.IGNORECASE)
_CAR_QUALIFIER_RE = re.compile(r"\b(car|auto|vehicle|ignition)\b", re.IGNORECASE)
_LOCKOUT_RE = re.compile(r"\blockout\b", re.IGNORECASE)
_KEY_RE = re.compile(r"\bkeys?\b", re.IGNORECASE)
# "lock install", "install...lock", "change...lock", "fix...lock", "rekey" --
# deliberately conservative: only recognized lock-installation phrasings,
# not a bare "lock" (which would false-match "lockout" text too, since
# "lockout" itself contains "lock").
_LOCK_INSTALL_RE = re.compile(
    r"\block\s*install|install\w*.*\block\b|change\w*.*\block\b|fix\w*.*\block\b|\brekey\b",
    re.IGNORECASE,
)


def normalize_skill_label(skill: str) -> str:
    """"Car Lockout" / "car lockout" / "car_lockout" all normalize to the
    same canonical "car_lockout" identifier for comparison purposes."""
    return re.sub(r"[\s-]+", "_", (skill or "").strip().lower())


def classify_service_categories(service_type: str) -> set:
    """Returns a deterministic set of zero or more KNOWN_SERVICE_CATEGORIES
    that `service_type` text confidently matches. A category is only ever
    added when the text is unambiguous about both the service kind
    (lockout/keys/installation) and, for lockout/keys, the car-vs-house
    qualifier -- text with both or neither qualifier present is left
    unclassified rather than guessed."""
    text = (service_type or "").strip()
    if not text:
        return set()

    categories = set()
    is_house = bool(_HOUSE_QUALIFIER_RE.search(text))
    is_car = bool(_CAR_QUALIFIER_RE.search(text))

    if _LOCK_INSTALL_RE.search(text):
        categories.add(LOCK_INSTALLATION)
        return categories  # a lock-installation phrasing is specific enough on its own

    if _LOCKOUT_RE.search(text):
        if is_house and not is_car:
            categories.add(HOUSE_LOCKOUT)
        elif is_car and not is_house:
            categories.add(CAR_LOCKOUT)
        # both or neither qualifier -> ambiguous, no category assigned
    elif _KEY_RE.search(text):
        if is_house and not is_car:
            categories.add(HOUSE_KEYS)
        elif is_car and not is_house:
            categories.add(CAR_KEYS)
        # ambiguous -> no category

    return categories
