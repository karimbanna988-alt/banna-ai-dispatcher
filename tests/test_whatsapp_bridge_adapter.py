"""Focused tests for whatsapp_bridge_adapter.py -- the narrow,
localhost-only listener between the isolated Node.js WhatsApp Shadow
Bridge and the Python dispatcher. No real WhatsApp session, no real
network beyond loopback HTTP the test itself drives. Every test points
OPERATOR_API_DB_PATH at a fresh tmp_path database -- never the real
data/dispatcher.db -- exercising the same real whatsapp_pending_observations
table storage.py provisions in production, not a mock."""

from __future__ import annotations

import http.client
import json
import threading
import time

import pytest

import whatsapp_bridge_adapter as adapter
from dispatcher import storage


@pytest.fixture()
def clean_adapter_state(monkeypatch, tmp_path):
    monkeypatch.setenv(adapter.ADAPTER_SECRET_ENV_VAR, "test-bridge-secret")
    monkeypatch.setenv("OPERATOR_API_DB_PATH", str(tmp_path / "adapter_test.db"))
    adapter._reset_state_for_tests()
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


def test_adapter_module_never_writes_jobs_or_events_or_calls_dispatcher_service():
    """This module durably stores observations in its own dedicated
    table (whatsapp_pending_observations, via storage.py's accessor
    functions) but must never write to jobs/events directly and must
    never call DispatcherService -- attaching an observation to a real
    job stays the existing, human-driven operator_api.py commercial/save
    route, untouched by this module."""
    import pathlib

    source = pathlib.Path("whatsapp_bridge_adapter.py").read_text(encoding="utf-8")
    assert "import sqlite3" not in source  # all SQL stays inside storage.py
    for forbidden in ("INSERT INTO jobs", "INSERT INTO events", "UPDATE jobs", "UPDATE events", "DispatcherService("):
        assert forbidden not in source  # mentioning it in prose (docstrings/comments) is fine; calling it is not


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


# ==================================================== LIVE RECEIVE FIX ====
# Regression coverage for the real live-pilot failure (2026-08-02, ticket
# JO5O-N7JA never appeared in the CRM): whatsapp-web.js's real
# Message.timestamp is a numeric Unix epoch, never a string -- the
# original validate_observation() demanded a string and silently 400'd
# every real message while every test (including this file's own
# `valid_payload()` default) used a string fixture that never matched
# real bridge traffic. Combined with the Node-side bug where the dedupe
# file was marked "seen" before delivery was even attempted, the message
# was received, deduped, permanently rejected by the adapter, and lost.
def test_validate_observation_accepts_a_real_numeric_timestamp(clean_adapter_state):
    """whatsapp-web.js's Message.timestamp is a number -- this is the
    exact real payload shape, not the string fixture every other test
    in this file (deliberately) still uses for backward compatibility."""
    cleaned = adapter.validate_observation(valid_payload(timestamp=1700000000))
    assert cleaned["timestamp"] == "1700000000"


def test_add_observation_accepts_a_real_numeric_timestamp_end_to_end(clean_adapter_state):
    result = adapter.add_observation(valid_payload(message_id="numeric-ts", timestamp=1700000000))
    assert result["accepted"] is True
    pending = adapter.list_pending()
    assert len(pending) == 1
    assert pending[0]["timestamp"] == "1700000000"


def test_validate_observation_rejects_boolean_timestamp(clean_adapter_state):
    """A bool is technically an int subclass in Python -- must not be
    silently accepted as a real timestamp."""
    with pytest.raises(adapter.AdapterError):
        adapter.validate_observation(valid_payload(timestamp=True))


# --------------------------------------------- DURABLE STORAGE / RESTART --
def test_add_observation_persists_to_the_real_table_not_memory(clean_adapter_state):
    adapter.add_observation(valid_payload())
    conn = storage.connect(adapter._db_path())
    try:
        row = conn.execute(
            "SELECT message_id, status FROM whatsapp_pending_observations WHERE message_id = ?",
            ("wamid.ABC123",),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[1] == "pending"


def test_accepted_observation_survives_a_simulated_crm_restart(clean_adapter_state):
    """A restart re-opens storage.connect() against the same on-disk
    file -- nothing in this module's own state (there is none left) can
    lose the row. Simulated here by simply opening a brand-new
    connection to the same path, exactly like a fresh operator_api.py
    process would."""
    adapter.add_observation(valid_payload())
    fresh_conn = storage.connect(adapter._db_path())
    try:
        pending_after_restart = storage.list_whatsapp_pending_observations(fresh_conn)
    finally:
        fresh_conn.close()
    assert len(pending_after_restart) == 1
    assert pending_after_restart[0]["message_id"] == "wamid.ABC123"


def test_duplicate_message_id_never_creates_a_second_row(clean_adapter_state):
    adapter.add_observation(valid_payload())
    adapter.add_observation(valid_payload())
    adapter.add_observation(valid_payload())
    conn = storage.connect(adapter._db_path())
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM whatsapp_pending_observations WHERE message_id = ?", ("wamid.ABC123",)
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_invalid_payload_never_creates_a_phantom_pending_row(clean_adapter_state):
    """A rejected (400) request must leave no trace -- the Node bridge
    does not mark a rejected message as seen, so it stays retryable; the
    Python side must not have quietly stored a partial/invalid row
    either."""
    bad_payload = valid_payload()
    del bad_payload["timestamp"]
    with pytest.raises(adapter.AdapterError):
        adapter.add_observation(bad_payload)
    assert adapter.list_pending(include_resolved=True) == []


# ------------------------------------------------- QUOTED-REPLY CORRELATION
def test_technician_reply_quoting_an_attached_job_message_is_correlated(clean_adapter_state):
    """A technician's reply (e.g. "k") that quotes the original dispatch
    message must surface the job it was attached to -- a hint for the
    operator, never an automatic status change or attach."""
    adapter.add_observation(valid_payload(message_id="original-job-post"))
    adapter.resolve_observation("original-job-post", "attached", job_id=42)

    reply = adapter.add_observation(valid_payload(
        message_id="tech-reply-1", text="k", quoted_message_id="original-job-post",
    ))
    assert reply["accepted"] is True
    stored = adapter.get_observation("tech-reply-1")
    assert stored["correlated_job_id"] == 42
    assert stored["job_id"] is None  # correlation is a hint, never an auto-attach


def test_reply_quoting_a_never_attached_message_has_no_correlation(clean_adapter_state):
    adapter.add_observation(valid_payload(message_id="original-job-post"))
    # Never attached -- no job_id recorded against it.
    reply = adapter.add_observation(valid_payload(
        message_id="tech-reply-1", text="k", quoted_message_id="original-job-post",
    ))
    assert reply["accepted"] is True
    stored = adapter.get_observation("tech-reply-1")
    assert stored["correlated_job_id"] is None


# --------------------------------------------------- ACKNOWLEDGEMENT-ONLY --
def test_bare_acknowledgement_reply_is_flagged_for_human_review_not_ignored():
    """'k' has no deterministic, tested business meaning -- it must never
    be silently dropped nor invented into a real technician status. It
    is stored as-is and flagged for a human to interpret."""
    import whatsapp_bridge_adapter as adapter_module

    assert adapter_module._acknowledgement_only("k") is True
    assert adapter_module._acknowledgement_only("  K  ") is True
    assert adapter_module._acknowledgement_only("kk") is True
    assert adapter_module._acknowledgement_only("On my way, 10 min out") is False


def test_add_observation_flags_bare_k_reply_human_review_needed(clean_adapter_state):
    result_obs = adapter.add_observation(valid_payload(message_id="k-reply", text="k"))
    assert result_obs["accepted"] is True
    stored = adapter.get_observation("k-reply")
    assert stored["human_review_needed"] is True
    assert stored["human_review_reason"] == "acknowledgement_only_reply"
    assert stored["status"] == "pending"  # still a real pending observation, not silently dropped


def test_add_observation_does_not_flag_an_informative_reply(clean_adapter_state):
    stored_result = adapter.add_observation(valid_payload(message_id="eta-reply", text="On my way, 10 min"))
    assert stored_result["accepted"] is True
    stored = adapter.get_observation("eta-reply")
    assert stored["human_review_needed"] is False


# ------------------------------------------------------------- DIRECTION --
def test_stored_observation_direction_is_always_incoming(clean_adapter_state):
    """Defense in depth: even if a caller tried to smuggle a different
    direction through the payload, this endpoint's only legitimate use
    is incoming messages -- direction is always set server-side, never
    trusted from the request body."""
    adapter.add_observation(valid_payload())
    stored = adapter.get_observation("wamid.ABC123")
    assert stored["direction"] == "incoming"


# ==================================================== OUTGOING/SELF AUDIT ==
def outgoing_payload(**overrides):
    payload = {
        "event_type": "outgoing_message",
        "message_id": "wamid.OUT1",
        "group_id": "111@g.us",
        "group_name": "Sebai Augusta Dispatch",
        "timestamp": "1700000000",
        "text": "ok sending tech now",
    }
    payload.update(overrides)
    return payload


def test_validate_outgoing_audit_accepts_a_well_formed_payload(clean_adapter_state):
    cleaned = adapter.validate_outgoing_audit(outgoing_payload())
    assert cleaned["message_id"] == "wamid.OUT1"


def test_validate_outgoing_audit_rejects_wrong_event_type(clean_adapter_state):
    with pytest.raises(adapter.AdapterError):
        adapter.validate_outgoing_audit(outgoing_payload(event_type="incoming_message"))


@pytest.mark.parametrize("field", ["message_id", "group_id", "group_name", "text"])
def test_validate_outgoing_audit_rejects_missing_required_field(clean_adapter_state, field):
    payload = outgoing_payload()
    del payload[field]
    with pytest.raises(adapter.AdapterError):
        adapter.validate_outgoing_audit(payload)


def test_add_outgoing_audit_never_appears_in_the_actionable_pending_queue(clean_adapter_state):
    """The core safety guarantee for Phase 4: an outgoing/self message can
    never become inbound work. Verified at the real storage layer, not
    mocked -- add_outgoing_audit routes to a completely separate table
    from add_observation."""
    adapter.add_outgoing_audit(outgoing_payload())
    assert adapter.list_pending(include_resolved=True) == []


def test_add_outgoing_audit_persists_and_dedupes(clean_adapter_state):
    from dispatcher import storage as storage_module

    conn = adapter._conn()
    try:
        first = adapter.add_outgoing_audit(outgoing_payload(), conn=conn)
        second = adapter.add_outgoing_audit(outgoing_payload(), conn=conn)
        assert first["duplicate"] is False
        assert second["duplicate"] is True
        assert len(storage_module.list_whatsapp_outgoing_audit(conn)) == 1
    finally:
        conn.close()


def test_http_endpoint_routes_outgoing_message_to_audit_not_observations(bridge_server):
    port = bridge_server.server_address[1]
    status, body = _http(
        port, "POST", "/observation", outgoing_payload(), headers={"X-Bridge-Secret": "test-bridge-secret"}
    )
    assert status == 200
    assert body["accepted"] is True
    assert adapter.list_pending() == []


def test_http_endpoint_still_routes_incoming_message_to_the_normal_pending_queue(bridge_server):
    port = bridge_server.server_address[1]
    status, body = _http(
        port, "POST", "/observation", valid_payload(), headers={"X-Bridge-Secret": "test-bridge-secret"}
    )
    assert status == 200
    assert len(adapter.list_pending()) == 1


def test_outgoing_audit_module_never_calls_resolve_or_attach_style_functions():
    """Static guard: the outgoing-audit code path must not reference any
    of the job-attaching/resolving functions, so a future edit can't
    accidentally wire outgoing traffic into the actionable flow without
    this test failing."""
    import inspect

    src = inspect.getsource(adapter.add_outgoing_audit)
    body_only = src.split('"""', 2)[-1]  # drop the docstring, which legitimately names these for explanation
    for forbidden in ("resolve_whatsapp_observation(", "add_whatsapp_observation(", "resolve_observation("):
        assert forbidden not in body_only
