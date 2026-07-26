from dispatcher.updates import answer_from_latest_status, map_status_to_reply


def test_going():
    r = map_status_to_reply("Going")
    assert r.confirmed is True
    assert r.reply == "Going"


def test_eta():
    r = map_status_to_reply("ETA 20")
    assert r.confirmed is True
    assert r.reply == "Tech ETA 20 min"


def test_no_answer():
    r = map_status_to_reply("No answer")
    assert r.reply == "No answer"


def test_on_phone():
    r = map_status_to_reply("On phone")
    assert r.reply == "Tech on phone"


def test_pass():
    r = map_status_to_reply("Pass")
    assert r.reply == "Pass"


def test_called_vs_calling_now():
    assert map_status_to_reply("Called").reply == "Tech called"
    assert map_status_to_reply("Calling now").reply == "Tech calling now"


def test_unrecognized_status_is_not_guessed():
    r = map_status_to_reply("the customer made us a sandwich")
    assert r.confirmed is False
    assert r.reply is None


def test_any_update_uses_latest_confirmed_status():
    latest = map_status_to_reply("Going")
    answer = answer_from_latest_status(latest)
    assert answer.reply == "Going"


def test_any_update_with_no_confirmed_status_is_flagged():
    answer = answer_from_latest_status(None)
    assert answer.confirmed is False
    assert "ask the dispatcher" in answer.reason


def test_did_anyone_call_uses_same_mechanism():
    latest = map_status_to_reply("Called")
    answer = answer_from_latest_status(latest)
    assert answer.reply == "Tech called"
