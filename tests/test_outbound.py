"""Tests for the disabled-by-default Quo outbound boundary
(src/dispatcher/outbound.py). No network call is ever made by this
module in its current form -- _send_live() is unreachable from
propose_send() and raises NotImplementedError if ever called directly.
These tests never load the real .env and never reference a real Quo
contact id."""

import pytest

from dispatcher import outbound, storage


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    c = storage.connect(path)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def clean_outbound_env(monkeypatch):
    """Every test starts from a fully clean, explicit environment --
    never inherits the real process environment or a real .env file."""
    for var in (
        outbound.OUTBOUND_MODE_ENV_VAR, outbound.KILL_SWITCH_ENV_VAR,
        outbound.SOURCE_ALLOWLIST_ENV_VAR, outbound.TEST_RECIPIENT_ALLOWLIST_ENV_VAR,
    ):
        monkeypatch.delenv(var, raising=False)


def make_request(source="AGN", job_id=None, quo_contact_id="TEST_CONTACT_1", text="K"):
    return outbound.build_request(source=source, job_id=job_id, quo_contact_id=quo_contact_id, text=text)


# ---------------------------------------------------------------- Default
def test_default_mode_is_disabled():
    assert outbound.current_mode() == outbound.OutboundMode.DISABLED


def test_disabled_mode_blocks_even_with_allowlisted_source(conn, monkeypatch):
    monkeypatch.setenv(outbound.SOURCE_ALLOWLIST_ENV_VAR, "AGN")
    result = outbound.propose_send(conn, make_request())
    assert result.outcome == "blocked"
    assert "disabled" in result.reason.lower()


def test_unrecognized_mode_value_falls_back_to_disabled(monkeypatch):
    monkeypatch.setenv(outbound.OUTBOUND_MODE_ENV_VAR, "totally-not-a-real-mode")
    assert outbound.current_mode() == outbound.OutboundMode.DISABLED


# ------------------------------------------------------------ Kill switch
def test_kill_switch_blocks_regardless_of_mode(conn, monkeypatch):
    monkeypatch.setenv(outbound.OUTBOUND_MODE_ENV_VAR, "preview")
    monkeypatch.setenv(outbound.SOURCE_ALLOWLIST_ENV_VAR, "AGN")
    monkeypatch.setenv(outbound.KILL_SWITCH_ENV_VAR, "on")
    result = outbound.propose_send(conn, make_request())
    assert result.outcome == "blocked"
    assert "kill switch" in result.reason.lower()


# --------------------------------------------------------------- Allowlist
def test_source_not_on_allowlist_is_blocked_even_in_preview(conn, monkeypatch):
    monkeypatch.setenv(outbound.OUTBOUND_MODE_ENV_VAR, "preview")
    # allowlist left empty -- nothing is allowed by default
    result = outbound.propose_send(conn, make_request(source="AGN"))
    assert result.outcome == "blocked"
    assert "allowlist" in result.reason.lower()


# ----------------------------------------------------------------- Preview
def test_preview_mode_shows_what_would_be_sent_and_makes_no_network_call(conn, monkeypatch):
    monkeypatch.setenv(outbound.OUTBOUND_MODE_ENV_VAR, "preview")
    monkeypatch.setenv(outbound.SOURCE_ALLOWLIST_ENV_VAR, "AGN")
    request = make_request(text="K")
    result = outbound.propose_send(conn, request)
    assert result.outcome == "previewed"
    assert result.request.text == "K"  # exact proposed content preserved for display

    row = conn.execute(
        "SELECT outcome, mode, proposed_text FROM quo_outbound_log WHERE idempotency_key = ?",
        (request.idempotency_key,),
    ).fetchone()
    assert row == ("previewed", "preview", "K")


# --------------------------------------------------------------- Approval
def test_approval_mode_without_approved_by_is_blocked(conn, monkeypatch):
    monkeypatch.setenv(outbound.OUTBOUND_MODE_ENV_VAR, "approval")
    monkeypatch.setenv(outbound.SOURCE_ALLOWLIST_ENV_VAR, "AGN")
    result = outbound.propose_send(conn, make_request())
    assert result.outcome == "blocked"
    assert "approved_by" in result.reason.lower()


def test_approval_mode_recipient_not_on_test_allowlist_is_blocked(conn, monkeypatch):
    monkeypatch.setenv(outbound.OUTBOUND_MODE_ENV_VAR, "approval")
    monkeypatch.setenv(outbound.SOURCE_ALLOWLIST_ENV_VAR, "AGN")
    result = outbound.propose_send(conn, make_request(quo_contact_id="NOT_ALLOWLISTED"), approved_by="dispatcher1")
    assert result.outcome == "blocked"
    assert "test-recipient allowlist" in result.reason.lower()


def test_approval_mode_never_sends_even_when_fully_configured(conn, monkeypatch):
    """Even with mode=approval, an allowlisted source, a test recipient,
    and an explicit approver, this build still refuses -- real sending is
    intentionally not implemented yet."""
    monkeypatch.setenv(outbound.OUTBOUND_MODE_ENV_VAR, "approval")
    monkeypatch.setenv(outbound.SOURCE_ALLOWLIST_ENV_VAR, "AGN")
    monkeypatch.setenv(outbound.TEST_RECIPIENT_ALLOWLIST_ENV_VAR, "TEST_CONTACT_1")
    result = outbound.propose_send(conn, make_request(quo_contact_id="TEST_CONTACT_1"), approved_by="dispatcher1")
    assert result.outcome == "blocked"
    assert "not implemented" in result.reason.lower()


def test_approval_mode_rejects_content_other_than_exact_k(conn, monkeypatch):
    monkeypatch.setenv(outbound.OUTBOUND_MODE_ENV_VAR, "approval")
    monkeypatch.setenv(outbound.SOURCE_ALLOWLIST_ENV_VAR, "AGN")
    monkeypatch.setenv(outbound.TEST_RECIPIENT_ALLOWLIST_ENV_VAR, "TEST_CONTACT_1")
    result = outbound.propose_send(
        conn, make_request(quo_contact_id="TEST_CONTACT_1", text="Hello there"), approved_by="dispatcher1",
    )
    assert result.outcome == "blocked"
    assert '"k"' in result.reason.lower()


def test_approval_mode_blocks_already_acknowledged_job(conn, monkeypatch):
    monkeypatch.setenv(outbound.OUTBOUND_MODE_ENV_VAR, "approval")
    monkeypatch.setenv(outbound.SOURCE_ALLOWLIST_ENV_VAR, "AGN")
    monkeypatch.setenv(outbound.TEST_RECIPIENT_ALLOWLIST_ENV_VAR, "TEST_CONTACT_1")
    result = outbound.propose_send(
        conn, make_request(quo_contact_id="TEST_CONTACT_1"), approved_by="dispatcher1", already_acknowledged=True,
    )
    assert result.outcome == "blocked"
    assert "already been acknowledged" in result.reason.lower()


def test_already_acknowledged_blocks_even_in_preview_mode(conn, monkeypatch):
    """The already-acknowledged guard applies regardless of mode -- it's
    checked before the mode branch at all."""
    monkeypatch.setenv(outbound.OUTBOUND_MODE_ENV_VAR, "preview")
    monkeypatch.setenv(outbound.SOURCE_ALLOWLIST_ENV_VAR, "AGN")
    result = outbound.propose_send(conn, make_request(), already_acknowledged=True)
    assert result.outcome == "blocked"
    assert "already been acknowledged" in result.reason.lower()


# ------------------------------------------------------------------- Live
def test_live_mode_is_always_blocked_in_this_build(conn, monkeypatch):
    monkeypatch.setenv(outbound.OUTBOUND_MODE_ENV_VAR, "live")
    monkeypatch.setenv(outbound.SOURCE_ALLOWLIST_ENV_VAR, "AGN")
    result = outbound.propose_send(conn, make_request())
    assert result.outcome == "blocked"
    assert "not implemented" in result.reason.lower()


def test_send_live_raises_if_ever_called_directly(conn):
    with pytest.raises(NotImplementedError):
        outbound._send_live(conn, make_request())


# -------------------------------------------------------------- Duplicate
def test_duplicate_request_is_recognized_and_not_recorded_twice(conn, monkeypatch):
    monkeypatch.setenv(outbound.OUTBOUND_MODE_ENV_VAR, "preview")
    monkeypatch.setenv(outbound.SOURCE_ALLOWLIST_ENV_VAR, "AGN")
    request = make_request(text="K")

    first = outbound.propose_send(conn, request)
    assert first.outcome == "previewed"

    second = outbound.propose_send(conn, request)
    assert second.outcome == "duplicate"

    count = conn.execute(
        "SELECT COUNT(*) FROM quo_outbound_log WHERE idempotency_key = ?", (request.idempotency_key,)
    ).fetchone()[0]
    assert count == 1


def test_idempotency_key_is_deterministic_per_content():
    a = outbound.generate_idempotency_key("AGN", 1, "CONTACT1", "K")
    b = outbound.generate_idempotency_key("AGN", 1, "CONTACT1", "K")
    c = outbound.generate_idempotency_key("AGN", 1, "CONTACT1", "different text")
    assert a == b
    assert a != c


# ------------------------------------------------- Every attempt recorded
def test_every_outcome_is_durably_recorded_before_any_send(conn, monkeypatch):
    """Blocked and previewed attempts are both recorded -- the audit trail
    covers every attempt, not only ones that would have sent."""
    monkeypatch.setenv(outbound.OUTBOUND_MODE_ENV_VAR, "disabled")
    blocked = outbound.propose_send(conn, make_request(quo_contact_id="C_BLOCKED"))
    assert blocked.outcome == "blocked"
    row = conn.execute(
        "SELECT outcome FROM quo_outbound_log WHERE idempotency_key = ?", (blocked.request.idempotency_key,)
    ).fetchone()
    assert row == ("blocked",)


# -------------------------------------------------- Structural safety net
def test_outbound_module_never_imports_the_ingestion_client():
    """Enforces "do not reuse ingestion methods for sending" structurally:
    outbound.py must not import quo_client (mentioning it in prose, e.g.
    to explain the separation, is fine -- an actual import is not)."""
    from pathlib import Path
    source = Path("src/dispatcher/outbound.py").read_text(encoding="utf-8")
    assert "import quo_client" not in source
    assert "from .quo_client" not in source
    assert "from dispatcher.quo_client" not in source


def test_outbound_module_never_references_the_api_key_value():
    from pathlib import Path
    source = Path("src/dispatcher/outbound.py").read_text(encoding="utf-8")
    assert "print(" not in source
    assert "QUO_API_KEY" not in source  # this module has no business reading the ingestion key at all
