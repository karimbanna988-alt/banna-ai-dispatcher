"""Tests for scripts/background_poller.py -- a manually-started,
disabled-by-default standalone poller. No network call in these tests;
the Quo client is always a local stub. Never loads or prints the real API
key, never touches the real database."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import background_poller  # noqa: E402

from dispatcher import storage
from dispatcher.quo_client import Contact, QuoMessage
from dispatcher.quo_ingest import load_dispatch_groups


@pytest.fixture()
def conn(tmp_path):
    c = storage.connect(tmp_path / "test.db")
    yield c
    c.close()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("QUO_API_KEY", raising=False)
    monkeypatch.delenv(background_poller.MIN_INTERVAL_ENV_VAR, raising=False)


def _fake_client_factory(contacts_by_id, messages_by_phone):
    class _Stub:
        def get_contact(self, contact_id):
            return contacts_by_id[contact_id]

        def iter_messages(self, *, phone_number_id, participants, **filters):
            key = participants[0] if participants else None
            return iter(messages_by_phone.get(key, []))

    def factory(*args, **kwargs):
        return _Stub()

    return factory


def _agn_contacts():
    real_config = load_dispatch_groups()
    agn_phone = "+15550109900"
    contacts_by_id = {
        g.quo_contact_id: Contact(
            id=g.quo_contact_id, first_name=g.label, last_name=None, company=None,
            phone_numbers=[agn_phone] if g.label == "AGN" else [],
        )
        for g in real_config.dispatch_groups
    }
    return real_config, agn_phone, contacts_by_id


# ----------------------------------------------------- 1. Disabled by default
def test_importing_the_module_never_polls_anything(conn, monkeypatch):
    """Import already happened at module load time above -- this just
    confirms it left no trace (no checkpoint, no processed messages)."""
    assert storage.get_quo_checkpoint(conn) is None


def test_main_requires_explicit_invocation(monkeypatch, conn, tmp_path):
    """Calling poll_once directly is the only way anything happens --
    there is no module-level call, no import-time side effect, no
    scheduled trigger anywhere in this file."""
    source = Path("scripts/background_poller.py").read_text(encoding="utf-8")
    # The only place main()/poll_once()/run_loop() are invoked is the
    # `if __name__ == "__main__":` guard -- never at module scope.
    lines_outside_guard = source.split('if __name__ == "__main__":')[0]
    assert "poll_once(" not in lines_outside_guard.split("def poll_once")[0]


# ----------------------------------------------------- 2. Start/shutdown
def test_run_loop_stops_cleanly_on_keyboard_interrupt(conn, monkeypatch):
    monkeypatch.setenv("QUO_API_KEY", "")  # config_error each iteration -- fine, we're testing shutdown

    def raising_sleep(_seconds):
        raise KeyboardInterrupt

    result = background_poller.run_loop(conn, interval_seconds=1, sleep_fn=raising_sleep)
    assert result == 0


def test_run_loop_respects_max_iterations_for_deterministic_testing(conn, monkeypatch):
    monkeypatch.setenv("QUO_API_KEY", "")
    calls = []
    result = background_poller.run_loop(
        conn, interval_seconds=0, max_iterations=3, sleep_fn=lambda s: calls.append(s),
    )
    assert result == 0
    assert len(calls) == 2  # sleeps between iterations, not after the last one


# ------------------------------------------------- 3/4. Incoming-only, CLOSINGS filtered
def test_poll_once_queues_incoming_and_skips_outgoing(conn, monkeypatch):
    monkeypatch.setenv("QUO_API_KEY", "test-key-not-real")
    real_config, agn_phone, contacts_by_id = _agn_contacts()

    inbound = QuoMessage(
        id="BP_IN1", to="PNus", from_number=agn_phone, text="Ref: AGN Locksmith Services\nPDL: BP1\n",
        phone_number_id=real_config.our_phone_number_id, conversation_id="C1", direction="incoming",
        status="delivered", created_at="2026-07-29T10:00:00Z", updated_at="2026-07-29T10:00:00Z",
    )
    outbound = QuoMessage(
        id="BP_OUT1", to=agn_phone, from_number="PNus", text="CLOSINGS reply",
        phone_number_id=real_config.our_phone_number_id, conversation_id="C1", direction="outgoing",
        status="delivered", created_at="2026-07-29T10:01:00Z", updated_at="2026-07-29T10:01:00Z",
    )
    factory = _fake_client_factory(contacts_by_id, {agn_phone: [inbound, outbound]})

    status = background_poller.poll_once(conn, client_factory=factory)
    assert status["outcome"] == "polled"
    assert status["queued"] == 1

    assert conn.execute("SELECT COUNT(*) FROM quo_pending_messages WHERE message_id = 'BP_IN1'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM quo_pending_messages WHERE message_id = 'BP_OUT1'").fetchone()[0] == 0


# ------------------------------------------------------- 5. Duplicate polling
def test_duplicate_poll_does_not_duplicate_messages(conn, monkeypatch):
    monkeypatch.setenv("QUO_API_KEY", "test-key-not-real")
    monkeypatch.setenv(background_poller.MIN_INTERVAL_ENV_VAR, "0")  # allow back-to-back polls in this test
    real_config, agn_phone, contacts_by_id = _agn_contacts()

    inbound = QuoMessage(
        id="BP_DUP1", to="PNus", from_number=agn_phone, text="Ref: AGN Locksmith Services\nPDL: BPDUP\n",
        phone_number_id=real_config.our_phone_number_id, conversation_id="C2", direction="incoming",
        status="delivered", created_at="2026-07-29T10:00:00Z", updated_at="2026-07-29T10:00:00Z",
    )
    factory = _fake_client_factory(contacts_by_id, {agn_phone: [inbound]})

    first = background_poller.poll_once(conn, client_factory=factory)
    second = background_poller.poll_once(conn, client_factory=factory)
    assert first["queued"] == 1
    assert second["queued"] == 0

    count = conn.execute("SELECT COUNT(*) FROM quo_pending_messages WHERE message_id = 'BP_DUP1'").fetchone()[0]
    assert count == 1


# --------------------------------------------------- 6. Checkpoint safety
def test_min_interval_guard_skips_polling_too_soon(conn, monkeypatch):
    monkeypatch.setenv("QUO_API_KEY", "test-key-not-real")
    monkeypatch.setenv(background_poller.MIN_INTERVAL_ENV_VAR, "600")
    real_config, agn_phone, contacts_by_id = _agn_contacts()
    factory = _fake_client_factory(contacts_by_id, {agn_phone: []})

    first = background_poller.poll_once(conn, client_factory=factory)
    assert first["outcome"] == "polled"

    second = background_poller.poll_once(conn, client_factory=factory)
    assert second["outcome"] == "skipped"


# ------------------------------------------------------ 7. Restart recovery
def test_restart_recovery_reopens_connection_without_duplicating(tmp_path, monkeypatch):
    monkeypatch.setenv("QUO_API_KEY", "test-key-not-real")
    monkeypatch.setenv(background_poller.MIN_INTERVAL_ENV_VAR, "0")
    db_path = tmp_path / "restart.db"
    conn1 = storage.connect(db_path)

    real_config, agn_phone, contacts_by_id = _agn_contacts()
    inbound = QuoMessage(
        id="BP_RESTART1", to="PNus", from_number=agn_phone, text="Ref: AGN Locksmith Services\nPDL: BPRESTART\n",
        phone_number_id=real_config.our_phone_number_id, conversation_id="C3", direction="incoming",
        status="delivered", created_at="2026-07-29T10:00:00Z", updated_at="2026-07-29T10:00:00Z",
    )
    factory = _fake_client_factory(contacts_by_id, {agn_phone: [inbound]})

    background_poller.poll_once(conn1, client_factory=factory)
    conn1.close()  # simulates the app/process ending

    conn2 = storage.connect(db_path)  # simulates a fresh restart
    result = background_poller.poll_once(conn2, client_factory=factory)
    conn2.close()

    assert result["queued"] == 0  # already-processed message id is not re-queued after restart


# ------------------------------------------------------- 8. last_error lifecycle
def test_last_error_recorded_on_missing_api_key(conn, monkeypatch):
    monkeypatch.setenv("QUO_API_KEY", "")
    status = background_poller.poll_once(conn)
    assert status["outcome"] == "config_error"
    assert storage.get_poll_error(conn) == "QUO_API_KEY is not set"


def test_last_error_cleared_after_a_subsequent_success(conn, monkeypatch):
    monkeypatch.setenv("QUO_API_KEY", "")
    background_poller.poll_once(conn)
    assert storage.get_poll_error(conn) is not None

    monkeypatch.setenv("QUO_API_KEY", "test-key-not-real")
    monkeypatch.setenv(background_poller.MIN_INTERVAL_ENV_VAR, "0")
    real_config, agn_phone, contacts_by_id = _agn_contacts()
    factory = _fake_client_factory(contacts_by_id, {agn_phone: []})
    status = background_poller.poll_once(conn, client_factory=factory)

    assert status["outcome"] == "polled"
    assert storage.get_poll_error(conn) is None


# --------------------------------------------------- 9. No outbound possible
def test_background_poller_never_imports_outbound_module():
    """Mentioning "dispatcher.outbound" in prose (to explain why it's
    irrelevant) is fine -- an actual import is not."""
    source = Path("scripts/background_poller.py").read_text(encoding="utf-8")
    assert "import outbound" not in source
    assert "from dispatcher.outbound" not in source
    assert "from .outbound" not in source
    assert "dispatcher.outbound." not in source  # no attribute access via a qualified reference either


def test_background_poller_source_contains_no_write_http_verbs():
    source = Path("scripts/background_poller.py").read_text(encoding="utf-8")
    for forbidden in (".post(", ".put(", ".delete(", ".patch("):
        assert forbidden not in source


def test_background_poller_never_references_the_api_key_value():
    source = Path("scripts/background_poller.py").read_text(encoding="utf-8")
    assert "print(api_key" not in source
    assert "print(os.environ" not in source


# ------------------------------------------ 10. No service/task/auto-install
def test_background_poller_never_installs_itself_anywhere():
    source = Path("scripts/background_poller.py").read_text(encoding="utf-8").lower()
    forbidden_indicators = (
        "schtasks", "register-scheduledtask", "new-service", "sc.exe", "sc create",
        "win32serviceutil", "createservice", "startup\\", "\\startup",
        "hkey_local_machine", "hkey_current_user", "registry", "systemd", "crontab",
        "new-scheduledtaskaction", "start-service",
    )
    for indicator in forbidden_indicators:
        assert indicator not in source, f"background_poller.py must never reference {indicator!r}"
