"""Tests for the shared deterministic message patterns.

These exist in one module so the production interpreter and the owner
demo cannot drift apart. Every input string below is a real message
observed during the shadow shifts, including the false positives that
shaped each pattern.
"""
import pytest

from dispatcher import message_patterns as mp


# ------------------------------------------------------------- ETA ----
@pytest.mark.parametrize("text,expected", [
    ("20 min", "20 min"),
    ("He just told me 2 mins", "2 mins"),
    ("It's gonna take 1.5 hours from now", "1.5 hours"),
    ("1:30 hours", "1:30"),
])
def test_stated_eta_reports_the_whole_phrase_not_the_bare_unit(text, expected):
    """Regression: delegating to the shared pattern once lost the
    quantity and displayed "20 min" as "(min)"."""
    assert mp.stated_eta(text) == expected


@pytest.mark.parametrize("text", ["8039446929", "1009 florence st", "3N1CN7AP9FL803116", "926", "quoted 125"])
def test_digits_without_time_language_are_not_an_eta(text):
    """The rule this replaced was "any digit means ETA" -- wrong ~94% of
    the time. Each string here is a real message it wrongly labelled."""
    assert mp.stated_eta(text) is None


# -------------------------------------------------------- EN ROUTE ----
@pytest.mark.parametrize("text", ["On my way", "omw", "Cuz on my way", "On the way still"])
def test_first_person_travel_is_en_route(text):
    assert mp.is_en_route(text) is True


def test_customer_arranging_another_locksmith_is_not_en_route():
    """The real false positive: this is the CUSTOMER's other locksmith."""
    assert mp.is_en_route("She has someone on the way") is False


def test_heading_to_a_different_job_is_not_en_route():
    assert mp.is_en_route("I will go to her after i'm done with the one i'm heading to") is False


# --------------------------------------------------------- CLOSING ----
@pytest.mark.parametrize("text,amount,method", [
    ("$200 cash abozeid", "200", "cash"),
    ("Done 180 cash", "180", "cash"),
    ("$379.50 cc joy", "379.50", "cc"),
    ("$415 venmo to hazem", "415", "venmo"),
    ("finished 220 card", "220", "card"),
])
def test_amount_adjacent_to_payment_word_is_a_closing(text, amount, method):
    assert mp.extract_closing_amount(text) == (amount, method)


@pytest.mark.parametrize("text", [
    "Da keda additional to the $150",
    "customer is arguing about the $150",
    "quoted 125",
    "all done",
])
def test_money_without_an_adjacent_payment_word_is_not_a_closing(text):
    """Adjacency is the whole safety property -- it is what stops a
    figure mentioned in passing from becoming a closing amount."""
    assert mp.extract_closing_amount(text) == (None, None)


def test_a_closing_needs_both_halves_or_neither():
    amount, method = mp.extract_closing_amount("job done, 200")
    assert amount is None and method is None


# ------------------------------------------------------- NO ANSWER ----
@pytest.mark.parametrize("text", [
    "No answer will try to call again",
    "Cx doesn't answer, voice mail",
    "Its going to voicemail left a voicemail and texted them",
    "No answer sent a text",
])
def test_real_no_answer_phrasings(text):
    assert mp.is_no_answer(text) is True


def test_leading_no_is_not_treated_as_a_decline_by_the_no_answer_rule():
    """"No answer sent a text" begins with "No" but is a report, not a
    refusal -- only a leading "pass" marks the dropping case."""
    assert mp.is_no_answer("No answer sent a text") is True
    assert mp.is_pass_prefixed("No answer sent a text") is False
    assert mp.is_pass_prefixed("Pass he's too far now not worth them not answering") is True


# --------------------------------------------------------- MENTION ----
def test_bare_mention_carries_no_status():
    assert mp.is_mention_only("@2632865341592") is True


def test_mention_with_real_content_is_not_mention_only():
    assert mp.is_mention_only("@2632865341592 code please osso") is False


# ------------------------------------------------------------- ACK ----
def test_acknowledgement_and_decline_come_from_technician_ack():
    """Reused rather than re-listed, so the two sides cannot disagree
    about what "K" means."""
    from dispatcher import technician_ack
    assert mp.ACKNOWLEDGEMENT_TEXTS is technician_ack.ACKNOWLEDGEMENT_TEXTS
    assert mp.DECLINE_TEXTS is technician_ack.DECLINE_TEXTS
    assert mp.is_acknowledgement("K") is True
    assert mp.is_decline("can't") is True


def test_module_is_pure_pattern_matching_with_no_side_effects():
    import pathlib
    source = pathlib.Path(mp.__file__).read_text(encoding="utf-8")
    for forbidden in ("sqlite3", "execute(", "open(", "requests", "INSERT", "UPDATE "):
        assert forbidden not in source
