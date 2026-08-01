"""Focused tests for whatsapp_bridge_adapter.py -- the narrow,
localhost-only listener between the isolated Node.js WhatsApp Shadow
Bridge and the Python dispatcher. No real WhatsApp session, no real
network beyond loopback HTTP the test itself drives, no real database
write anywhere in this file (this module never touches
data/dispatcher.db at all -- see its own module docstring)."""

from __future__ import annotations

import http.client
import json
import threading
import time

import pytest

import whatsapp_bridge_adapter as adapter


@pytest.fixture()
def clean_adapter_state(monkeypatch):
    adapter._reset_state_for_tests()
    monkeypatch.setenv(adapter.ADAPTER_SECRET_ENV_VAR, "test-bridge-secret")
    yield
    adapter._reset_state_for_tests()


def valid_payload(**overrides):
    payload = {
        "event_type": "incoming_message",
        "message_id": "wamid.ABC123",
        "group_id": "111@g.us",
        "group_name": "Kabaka Dispatch",
        "sender": "15555550100",
        "timestamp": "1700000000",
        "text": "PDL: IYRT1 -- job done, customer paid $95 cash",
    }
    payload.update(overrides)
    return payload


# =============================================================== VALIDATION
def test_validate_observation_accepts_a_well_formed_payload(clean_adapter_state):
    cleaned = adapter.validate_observation(valid_payload())
    assert cleaned["message_id"] == "wamid.ABC123"
    assert cleaned["text"] == "PDL: IYRT1 -- job done, customer paid $95 cash"


def test_validate_observation_rejects_unknown_event_type(clean_adapter_state):
    with pytest.raises(adapter.AdapterError):
        adapter.validate_observation(valid_payload(event_type="send_message"))


@pytest.mark.parametrize("field", ["message_id", "group_id", "group_name", "text", "timestamp"])
def test_validate_observation_rejects_missing_required_field(clean_adapter_state, field):
    payload = valid_payload()
    del payload[field]
    with pytest.raises(adapter.AdapterError):
        adapter.validate_observation(payload)


def test_validate_observation_rejects_oversized_text(clean_adapter_state):
    with pytest.raises(adapter.AdapterError):
        adapter.validate_observation(valid_payload(text="x" * 5000))


def test_validate_observation_never_produces_a_job_status_or_attach_field(clean_adapter_state):
    """Structural: the cleaned observation dict must never contain a
    field that could be mistaken for an already-applied job action --
    proves this module cannot silently change a job's status."""
    cleaned = adapter.validate_observation(valid_payload())
    assert "job_status" not in cleaned
    assert "attached" not in cleaned
    assert "job_id" not in cleaned


# ================================================================ DEDUP ===
def test_add_observation_dedupes_by_message_id(clean_adapter_state):
    first = adapter.add_observation(valid_payload())
    second = adapter.add_observation(valid_payload())
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert len(adapter.list_pending()) == 1


def test_add_observation_preserves_exact_original_text(clean_adapter_state):
    raw_text = "  Job done -- customer paid $95 CASH, no PDL mentioned  "
    adapter.add_observation(valid_payload(message_id="m-verbatim", text=raw_text))
    pending = adapter.list_pending()
    assert pending[0]["text"] == raw_text


# ============================================================ PENDING QUEUE
def test_new_observation_starts_pending_never_auto_attached(clean_adapter_state):
    adapter.add_observation(valid_payload())
    pending = adapter.list_pending()
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"


def test_resolve_observation_marks_attached_and_removes_from_pending_list(clean_adapter_state):
    adapter.add_observation(valid_payload())
    assert len(adapter.list_pending()) == 1
    resolved = adapter.resolve_observation("wamid.ABC123", "attached")
    assert resolved["status"] == "attached"
    assert adapter.list_pending() == []  # no longer "pending"
    assert len(adapter.list_pending(include_resolved=True)) == 1


def test_resolve_observation_returns_none_for_unknown_message_id(clean_adapter_state):
    assert adapter.resolve_observation("does-not-exist", "ignored") is None


# =================================================== LOOPBACK HTTP SERVER =
@pytest.fixture()
def bridge_server(clean_adapter_state):
    server = adapter.run_bridge_adapter(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _http(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdrs = dict(headers or {})
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    raw = resp.read()
    parsed = json.loads(raw) if raw else None
    conn.close()
    return resp.status, parsed


def test_bridge_adapter_binds_loopback_only(bridge_server):
    assert bridge_server.server_address[0] == "127.0.0.1"


def test_health_endpoint_requires_no_secret(bridge_server):
    port = bridge_server.server_address[1]
    status, body = _http(port, "GET", "/health")
    assert status == 200
    assert body == {"status": "ok"}


def test_observation_rejected_without_correct_secret(bridge_server):
    port = bridge_server.server_address[1]
    status, body = _http(port, "POST", "/observation", valid_payload(), headers={"X-Bridge-Secret": "wrong"})
    assert status == 401


def test_observation_accepted_with_correct_secret_and_appears_in_pending(bridge_server):
    port = bridge_server.server_address[1]
    status, body = _http(port, "POST", "/observation", valid_payload(), headers={"X-Bridge-Secret": "test-bridge-secret"})
    assert status == 200
    assert body["accepted"] is True
    assert len(adapter.list_pending()) == 1


def test_malformed_json_body_rejected_not_crashed(bridge_server):
    port = bridge_server.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/observation", body="{not valid json", headers={"Content-Type": "application/json", "X-Bridge-Secret": "test-bridge-secret"})
    resp = conn.getresponse()
    assert resp.status == 400
    conn.close()


def test_rate_limit_kicks_in_after_repeated_requests(bridge_server, monkeypatch):
    monkeypatch.setattr(adapter, "RATE_LIMIT_MAX_REQUESTS", 3)
    port = bridge_server.server_address[1]
    statuses = []
    for i in range(5):
        status, _ = _http(port, "POST", "/observation", valid_payload(message_id=f"m-{i}"), headers={"X-Bridge-Secret": "test-bridge-secret"})
        statuses.append(status)
    assert 429 in statuses


def test_no_customer_data_in_server_logs(bridge_server, capsys):
    port = bridge_server.server_address[1]
    _http(port, "POST", "/observation", valid_payload(text="Customer Jane Doe, phone 555-1234"), headers={"X-Bridge-Secret": "test-bridge-secret"})
    captured = capsys.readouterr()
    assert "Jane Doe" not in (captured.out + captured.err)
    assert "555-1234" not in (captured.out + captured.err)


# ============================================================== STRUCTURAL
def test_adapter_module_never_imports_a_whatsapp_client_library():
    import pathlib

    source = pathlib.Path("whatsapp_bridge_adapter.py").read_text(encoding="utf-8")
    import_lines = [l for l in source.splitlines() if l.strip().startswith(("import ", "from "))]
    for bad in ("whatsapp-web.js", "puppeteer", "selenium", "playwright"):
        assert not any(bad in line for line in import_lines)


def test_adapter_module_owns_no_dispatcher_database_sql():
    """This module must never write to the real dispatcher database --
    it only ever holds observations in memory until a human, via the
    existing operator_api.py commercial/save route, does so through
    DispatcherService."""
    import pathlib

    source = pathlib.Path("whatsapp_bridge_adapter.py").read_text(encoding="utf-8")
    for forbidden in ("import sqlite3", "INSERT INTO", "storage.connect", "dispatcher.storage"):
        assert forbidden not in source


# ============================================= SECRET LOADING / NO ROTATION
# Regression coverage for the live-pilot bridge/adapter secret mismatch:
# the Node bridge never actually loaded whatsapp_shadow_bridge/.env into
# its process (see whatsapp_shadow_bridge/test/envFileLoading.test.js for
# that half of the fix). On the Python side, _get_or_create_adapter_secret()
# only ever trusts os.environ -- it never parses the .env file itself --
# so its restart-safety depends entirely on operator_api.py's own
# load_dotenv(REPO_ROOT / ".env") call running before it, every time.
# These tests prove that chain: an already-persisted secret is reused
# verbatim across repeated "restarts" (never regenerated, never
# duplicated in the file, unrelated entries left alone).
def test_get_or_create_adapter_secret_does_not_rotate_across_simulated_restarts(tmp_path, monkeypatch):
    from dotenv import load_dotenv

    env_path = tmp_path / ".env"
    env_path.write_text("SOME_OTHER_VAR=unrelated\n", encoding="utf-8")
    monkeypatch.delenv(adapter.ADAPTER_SECRET_ENV_VAR, raising=False)

    # "First startup": no secret yet anywhere -- generates and persists one.
    load_dotenv(env_path)
    first = adapter._get_or_create_adapter_secret(env_path)
    assert "SOME_OTHER_VAR=unrelated" in env_path.read_text(encoding="utf-8")

    # "Second startup": a fresh process wouldn't carry the env var forward
    # on its own -- only load_dotenv() re-reading the persisted file does.
    monkeypatch.delenv(adapter.ADAPTER_SECRET_ENV_VAR, raising=False)
    load_dotenv(env_path)
    second = adapter._get_or_create_adapter_secret(env_path)

    assert first == second
    contents = env_path.read_text(encoding="utf-8")
    assert contents.count(f"{adapter.ADAPTER_SECRET_ENV_VAR}=") == 1  # never re-appended
    assert "SOME_OTHER_VAR=unrelated" in contents  # unrelated entries untouched


def test_get_or_create_adapter_secret_generates_only_when_truly_absent(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.delenv(adapter.ADAPTER_SECRET_ENV_VAR, raising=False)

    generated = adapter._get_or_create_adapter_secret(env_path)
    assert len(generated) > 0
    assert env_path.read_text(encoding="utf-8").count(f"{adapter.ADAPTER_SECRET_ENV_VAR}=") == 1


def test_wrong_secret_response_and_server_logs_never_contain_the_real_secret_value(bridge_server, capsys):
    port = bridge_server.server_address[1]
    status, body = _http(port, "POST", "/observation", valid_payload(), headers={"X-Bridge-Secret": "a-wrong-guess"})
    assert status == 401
    assert "test-bridge-secret" not in json.dumps(body)
    captured = capsys.readouterr()
    assert "test-bridge-secret" not in (captured.out + captured.err)


def test_node_and_python_agree_on_the_bridge_secret_header_name():
    import pathlib

    node_source = pathlib.Path("whatsapp_shadow_bridge/src/adapterClient.js").read_text(encoding="utf-8")
    python_source = pathlib.Path("whatsapp_bridge_adapter.py").read_text(encoding="utf-8")
    assert "X-Bridge-Secret" in node_source
    assert "X-Bridge-Secret" in python_source


def test_bridge_launch_scripts_load_the_env_file_before_starting_node():
    """Regression guard: whatsapp_shadow_bridge/scripts/start_pairing.ps1
    and start_normal.ps1 must load whatsapp_shadow_bridge/.env into the
    Node process before it starts, or WHATSAPP_SHADOW_ADAPTER_SECRET
    silently falls back to "" and every /observation POST 401s -- exactly
    the defect this test suite is guarding against."""
    import pathlib
    import re

    scripts_dir = pathlib.Path("whatsapp_shadow_bridge/scripts")
    pattern = re.compile(r"node\s+--env-file(-if-exists)?=\.env\s+src[\\/]index\.js")
    for name in ("start_pairing.ps1", "start_normal.ps1"):
        source = (scripts_dir / name).read_text(encoding="utf-8")
        assert pattern.search(source), f"{name} must load whatsapp_shadow_bridge/.env before starting the bridge"
