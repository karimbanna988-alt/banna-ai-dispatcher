"""Unit tests for the read-only Quo API client. Zero real network calls --
every HTTP interaction goes through a FakeSession that returns canned
responses."""

from pathlib import Path

import pytest

from dispatcher.quo_client import (
    QuoAPIError,
    QuoClient,
    QuoConfigError,
    QuoRateLimitError,
)

MODULE_PATH = Path(__file__).parent.parent / "src" / "dispatcher" / "quo_client.py"


class FakeResponse:
    def __init__(self, status_code, json_body=None, text=""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text or str(json_body)

    def json(self):
        if self._json_body is None:
            raise ValueError("no JSON body")
        return self._json_body


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, headers, params, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "params": dict(params), "timeout": timeout})
        if not self._responses:
            raise AssertionError("FakeSession ran out of canned responses")
        return self._responses.pop(0)


# --------------------------------------------------------------- Config ----
def test_missing_api_key_raises_before_any_network_call(monkeypatch):
    monkeypatch.delenv("QUO_API_KEY", raising=False)
    session = FakeSession([])
    with pytest.raises(QuoConfigError):
        QuoClient(session=session)
    assert session.calls == []


def test_explicit_api_key_does_not_require_env_var(monkeypatch):
    monkeypatch.delenv("QUO_API_KEY", raising=False)
    session = FakeSession([FakeResponse(200, {"data": [], "nextPageToken": None})])
    client = QuoClient(api_key="explicit-key", session=session, sleep_fn=lambda s: None)
    list(client.iter_conversations(max_results=1))
    assert session.calls[0]["headers"]["Authorization"] == "explicit-key"


# ---------------------------------------------------------------- Auth -----
def test_auth_header_is_raw_key_not_bearer(monkeypatch):
    monkeypatch.setenv("QUO_API_KEY", "raw-key-123")
    session = FakeSession([FakeResponse(200, {"data": [], "nextPageToken": None})])
    client = QuoClient(session=session, sleep_fn=lambda s: None)
    list(client.iter_conversations(max_results=1))
    assert session.calls[0]["headers"] == {"Authorization": "raw-key-123"}


# ----------------------------------------------------------- Pagination ----
def test_iter_conversations_follows_next_page_token(monkeypatch):
    monkeypatch.setenv("QUO_API_KEY", "k")
    session = FakeSession([
        FakeResponse(200, {"data": [{"id": "c1", "phoneNumberId": "PN1", "participants": ["+1555"]}], "nextPageToken": "tok2"}),
        FakeResponse(200, {"data": [{"id": "c2", "phoneNumberId": "PN2", "participants": ["+1666"]}], "nextPageToken": None}),
    ])
    client = QuoClient(session=session, sleep_fn=lambda s: None)
    convos = list(client.iter_conversations(max_results=100))
    assert [c.id for c in convos] == ["c1", "c2"]
    assert len(session.calls) == 2
    assert session.calls[1]["params"]["pageToken"] == "tok2"


def test_iter_messages_follows_next_page_token(monkeypatch):
    monkeypatch.setenv("QUO_API_KEY", "k")
    session = FakeSession([
        FakeResponse(200, {"data": [{"id": "m1", "direction": "incoming", "text": "hi"}], "nextPageToken": "tok2"}),
        FakeResponse(200, {"data": [{"id": "m2", "direction": "incoming", "text": "bye"}], "nextPageToken": None}),
    ])
    client = QuoClient(session=session, sleep_fn=lambda s: None)
    messages = list(client.iter_messages(phone_number_id="PN1", participants=["+1555"], max_results=100))
    assert [m.id for m in messages] == ["m1", "m2"]


def test_bare_list_envelope_is_accepted():
    session = FakeSession([FakeResponse(200, [{"id": "c1", "phoneNumberId": "PN1", "participants": []}])])
    client = QuoClient(api_key="k", session=session, sleep_fn=lambda s: None)
    convos = list(client.iter_conversations(max_results=1))
    assert [c.id for c in convos] == ["c1"]


# ------------------------------------------------------------ Rate limit ---
def test_429_is_retried_then_succeeds():
    sleeps = []
    session = FakeSession([
        FakeResponse(429, text="rate limited"),
        FakeResponse(429, text="rate limited"),
        FakeResponse(200, {"data": [], "nextPageToken": None}),
    ])
    client = QuoClient(api_key="k", session=session, sleep_fn=sleeps.append)
    list(client.iter_conversations(max_results=1))
    assert len(session.calls) == 3
    assert len(sleeps) >= 2  # backoff sleeps happened (plus possible throttle sleeps)


def test_429_exhausted_raises_rate_limit_error():
    session = FakeSession([FakeResponse(429, text="rate limited")] * 10)
    client = QuoClient(api_key="k", session=session, sleep_fn=lambda s: None)
    with pytest.raises(QuoRateLimitError):
        list(client.iter_conversations(max_results=1))


# -------------------------------------------------------------- Errors -----
def test_non_2xx_raises_quo_api_error_with_status_and_body():
    session = FakeSession([FakeResponse(401, text="unauthorized")])
    client = QuoClient(api_key="k", session=session, sleep_fn=lambda s: None)
    with pytest.raises(QuoAPIError) as exc_info:
        list(client.iter_conversations(max_results=1))
    assert exc_info.value.status_code == 401
    assert "unauthorized" in exc_info.value.body


def test_non_json_response_raises_quo_api_error():
    session = FakeSession([FakeResponse(200, json_body=None, text="not json")])
    client = QuoClient(api_key="k", session=session, sleep_fn=lambda s: None)
    with pytest.raises(QuoAPIError):
        list(client.iter_conversations(max_results=1))


# -------------------------------------------------- Structural GET-only ----
def test_module_source_contains_no_write_http_verbs():
    """The safety guarantee for receive-only mode is structural: this
    module must never be able to send/modify anything on Quo. Guard it at
    the test level too, so a future edit can't silently add that
    capability."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    for forbidden in (".post(", ".put(", ".delete(", ".patch("):
        assert forbidden not in source, f"quo_client.py must never call {forbidden!r}"


# -------------------------------------------------------------- Contacts ---
def test_get_contact_parses_default_fields_and_phone_numbers():
    session = FakeSession([FakeResponse(200, {
        "data": {
            "id": "C1",
            "defaultFields": {
                "firstName": "Lucky", "lastName": None, "company": None,
                "phoneNumbers": [{"name": "main", "value": "+15551234567", "id": "P1"}],
            },
        },
    })])
    client = QuoClient(api_key="k", session=session, sleep_fn=lambda s: None)
    contact = client.get_contact("C1")
    assert contact.id == "C1"
    assert contact.label == "Lucky"
    assert contact.phone_numbers == ["+15551234567"]
    assert session.calls[0]["url"].endswith("/contacts/C1")


def test_get_contact_with_no_phone_numbers_returns_empty_list():
    session = FakeSession([FakeResponse(200, {
        "data": {"id": "C2", "defaultFields": {"firstName": "No Phone"}},
    })])
    client = QuoClient(api_key="k", session=session, sleep_fn=lambda s: None)
    contact = client.get_contact("C2")
    assert contact.phone_numbers == []


def test_get_contact_label_combines_first_last_company():
    session = FakeSession([FakeResponse(200, {
        "data": {
            "id": "C3",
            "defaultFields": {"firstName": "AGN", "lastName": "LEADS", "company": "AGN Locksmith"},
        },
    })])
    client = QuoClient(api_key="k", session=session, sleep_fn=lambda s: None)
    contact = client.get_contact("C3")
    assert contact.label == "AGN LEADS AGN Locksmith"
