"""Tests for the operator waiting-state read-model.

This projection decides the words above a job card on the phone. It is
presentation only -- but it is presentation the operator acts on, so the
safety ordering (a backend HUMAN_REVIEW always wins over anything read
out of message text) is pinned here, not just documented.
"""
import pytest

from dispatcher import operator_stage


def ws(**kw):
    return operator_stage.waiting_state(**kw)


# ------------------------------------------------- safety ordering ----
def test_backend_human_review_beats_any_message_text():
    """The worst possible bug here: a cheerful 'ETA 20 min' painted over
    a job the backend already said it could not resolve."""
    s = ws(next_action="HUMAN_REVIEW_NEEDED", latest_technician_update="20 min")
    assert s["label"] == "Human review"
    assert s["tone"] == "rev"


def test_backend_human_review_beats_an_explicit_closing_amount():
    s = ws(next_action="HUMAN_REVIEW_NEEDED", latest_technician_update="done 180 cash")
    assert s["label"] == "Human review"


def test_a_decline_is_never_shown_as_progress():
    s = ws(next_action="AWAITING_TECHNICIAN_K", latest_technician_update="cannot")
    assert s["label"] == "Tech declined"
    assert s["tone"] == "bad"


# ------------------------------- the real read-model shape (a DICT) ---
def test_latest_technician_update_is_a_dict_not_a_string():
    """REGRESSION. luxor_desk_read_model.get_latest_technician_update
    returns {update_type, raw_message, eta_minutes, created_at}. Treating
    it as a string raised AttributeError inside /api/active and took the
    whole route down -- the board would not load at all."""
    s = ws(latest_technician_update={
        "update_type": "ETA", "raw_message": "20 min out",
        "eta_minutes": 20, "created_at": "2026-08-14T18:51:36Z"})
    assert s["label"] == "ETA 20 min"


def test_a_recorded_update_type_outranks_rereading_the_text():
    """A human already classified this message. Re-deriving meaning from
    the raw text could contradict them."""
    s = ws(latest_technician_update={"update_type": "No Answer", "raw_message": "20 min"})
    assert s["label"] == "Customer no answer"


@pytest.mark.parametrize("update_type,expected", [
    ("Going", "En route"),
    ("No Answer", "Customer no answer"),
    ("No Show", "Customer no show"),
    ("Not Available", "Tech not available"),
    ("Customer Cancelled", "Customer cancelled"),
])
def test_every_recorded_update_type_has_operator_words(update_type, expected):
    assert ws(latest_technician_update={"update_type": update_type, "raw_message": ""})["label"] == expected


def test_completed_with_money_facts_is_a_closing_and_without_them_is_not():
    done = {"update_type": "Completed", "raw_message": "Done 180 cash"}
    assert ws(latest_technician_update=done)["label"] == "Closing $180 cash"
    bare = {"update_type": "Completed", "raw_message": "Done"}
    assert ws(latest_technician_update=bare)["label"] == "Done - amount missing"
    assert ws(latest_technician_update=bare)["tone"] == "rev"


def test_a_custom_update_falls_through_to_the_shared_patterns():
    s = ws(latest_technician_update={"update_type": "Custom", "raw_message": "on my way"})
    assert s["label"] == "En route"


def test_every_declared_update_type_is_handled():
    """If a new type is added to TECHNICIAN_UPDATE_TYPES it must get
    operator words, not fall silently into a generic bucket."""
    from dispatcher.action_names import TECHNICIAN_UPDATE_TYPES

    for update_type in TECHNICIAN_UPDATE_TYPES:
        if update_type == "Custom":
            continue  # deliberately unclassified -- falls through
        result = operator_stage._from_update_type(update_type, "", None)
        assert result is not None, f"{update_type} has no operator wording"


def test_an_empty_or_missing_update_is_safe():
    for value in (None, "", {}, {"update_type": None, "raw_message": None}):
        assert ws(status="In Progress", latest_technician_update=value)["label"]


# ------------------------------------------- the required vocabulary --
@pytest.mark.parametrize("text,expected", [
    ("K", "Tech accepted"),
    ("20 min", "ETA 20 min"),
    ("on my way", "En route"),
    ("Quoted 195 225", "Price quoted"),
    ("Hwa no answer brdo", "Customer no answer"),
])
def test_technician_messages_become_operator_words(text, expected):
    assert ws(latest_technician_update=text)["label"] == expected


def test_en_route_carries_the_stated_time_when_there_is_one():
    s = ws(latest_technician_update="on my way 15 min")
    assert s["label"].startswith("En route")
    assert "15" in s["label"]


def test_waiting_for_tech_k_comes_from_the_backend_label():
    s = ws(next_action="AWAITING_TECHNICIAN_K")
    assert s["label"] == "Waiting for tech K"
    assert s["next_expected"] == "the technician to accept"


def test_a_company_update_owed_is_surfaced():
    assert ws(company_update_owed={"owed": True})["label"] == "Update company"


# --------------------------------------------------- closing rules ----
def test_an_explicit_amount_and_method_read_as_a_closing():
    s = ws(latest_technician_update="Done 180 cash")
    assert s["label"] == "Closing $180 cash"
    assert s["tone"] == "ok"


def test_bare_done_is_never_a_closing():
    """The negative case the owner demo has to prove: 'Done' with no
    money facts must read as missing, not as complete."""
    s = ws(latest_technician_update="Done")
    assert s["label"] == "Done - amount missing"
    assert s["tone"] == "rev"
    assert "amount" in s["next_expected"]


def test_no_amount_is_ever_invented():
    for text in ("Done", "finished", "completed the job"):
        assert "$" not in ws(latest_technician_update=text)["label"]


# -------------------------------------------------------- fallback ----
def test_falls_back_to_backend_truth_not_to_a_guess():
    assert ws(bucket="AWAITING_CLOSING")["label"] == "Awaiting Closing"
    assert ws(status="In Progress")["label"] == "In Progress"


def test_an_unreadable_update_does_not_invent_a_state():
    s = ws(status="In Progress", latest_technician_update="asdfgh qwerty")
    assert s["label"] == "In Progress"


def test_every_result_has_the_three_render_keys():
    for kwargs in ({}, {"status": "In Progress"}, {"latest_technician_update": "K"},
                   {"next_action": "HUMAN_REVIEW_NEEDED"}):
        s = ws(**kwargs)
        assert set(s) == {"label", "tone", "next_expected"}
        assert isinstance(s["label"], str) and s["label"]


# ----------------------------------------------------- boundaries -----
def test_the_module_holds_no_database_access():
    """It is a pure projection. If it ever grows a connection it can no
    longer be trusted as presentation-only."""
    import inspect

    source = inspect.getsource(operator_stage)
    for forbidden in ("sqlite3", "get_conn", "SELECT ", "INSERT ", "conn."):
        assert forbidden not in source, f"operator_stage must stay pure ({forbidden})"


def test_it_reuses_the_shared_patterns_rather_than_its_own_regexes():
    """Business meaning lives in message_patterns. A private regex here
    would fork it and let the phone disagree with the brain."""
    import inspect

    source = inspect.getsource(operator_stage)
    assert "message_patterns" in source
    assert "re.compile" not in source
