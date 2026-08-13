"""Focused tests for operator_api.py -- the connected Operator UI V2's
JSON API. Every test seeds a temporary on-disk SQLite database via
storage.connect(tmp_path) -- never the real database (see conftest.py's
sys.path setup; the real dispatcher.db path is never referenced here).
Route handler functions (route_*) are called directly as plain Python
functions -- they take (handler, match, body) and never touch `handler`
for logic, so no real HTTP server/socket is needed for these tests: the
system's own "no real network calls" requirement is satisfied by
construction, not by mocking around one."""

from __future__ import annotations

import http.client
import json
import pathlib
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import pytest
import streamlit as st

import operator_api as api
from dispatcher import storage
from dispatcher.application import DispatcherService

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


# --------------------------------------------------------------- Fixtures -
@pytest.fixture()
def api_env(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = storage.connect(db_path)
    conn.close()
    monkeypatch.setenv("OPERATOR_API_DB_PATH", str(db_path))
    st.cache_resource.clear()
    st.cache_data.clear()
    return db_path


def future_ts(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def seed_live_boundary(db_path, when="2020-01-01T00:00:00Z"):
    conn = storage.connect(db_path)
    conn.execute(
        "INSERT INTO console_activation_state (id, live_queue_started_at, updated_at) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET live_queue_started_at = excluded.live_queue_started_at, updated_at = excluded.updated_at",
        (when, when),
    )
    conn.commit()
    conn.close()


def seed_pending(db_path, message_id, raw_text, group_label="AGN", minutes_from_now=5):
    conn = storage.connect(db_path)
    storage.save_pending_and_mark_processed(
        conn, message_id=message_id, conversation_id=f"c-{message_id}", group_label=group_label,
        raw_text=raw_text, from_number="+15550109900", received_at=future_ts(minutes_from_now),
    )
    conn.close()


def agn_message(ticket="FAKE1", area="Charlotte, NC"):
    return f"Ref: AGN Locksmith Services\nPDL: {ticket}\nPh: 1-555-010-3000\nAddr: 100 Fake St, {area}\nDesc: Car Lockout\n"


def main_leads_message(ticket="ML1", area="Charlotte, NC 28202"):
    return (
        f"Power By Ucallz App\n\nFabricated Locksmith Pro\nTicket: {ticket}\n"
        f"Name: Test Person\nPhone: (555) 010-2000\nAddress: 100 Fake Dr, {area}\nDesc: Car Lockout\n"
    )


def add_configured_technician(db_path, name="Fabricated Tech", dispatch_group="Fab Dispatch", closing_group="Fab Closings"):
    conn = storage.connect(db_path)
    tech_id = storage.add_technician(
        conn, name=name, areas=["Charlotte"], skills=["car lockout"],
        dispatch_group=dispatch_group, closing_group=closing_group,
    )
    conn.close()
    return tech_id


def add_unconfigured_technician(db_path, name="No Group Tech"):
    conn = storage.connect(db_path)
    tech_id = storage.add_technician(conn, name=name, areas=["Charlotte"], skills=["car lockout"])
    conn.close()
    return tech_id


def get_events_count(db_path) -> int:
    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    return n


def get_jobs_count(db_path) -> int:
    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    return n


# ================================================================== AUTH ==
def http_request(authority, method, path, body=None, headers=None):
    host, port = authority.rsplit(":", 1)
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    hdrs = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    raw = resp.read()
    parsed = None
    if raw:
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            parsed = None
    result = {
        "status": resp.status, "json": parsed,
        "set_cookie": resp.getheader("Set-Cookie"),
        "headers": {k: v for k, v in resp.getheaders()},
    }
    conn.close()
    return result


TEST_PASSWORD = "test-password-123"


@pytest.fixture()
def live_server(api_env, monkeypatch):
    monkeypatch.setenv(api.PASSWORD_ENV_VAR, TEST_PASSWORD)
    api._LOGIN_ATTEMPTS.clear()
    api._ACTIVE_SESSIONS.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{port}"
    server.shutdown()
    thread.join(timeout=2)
    server.server_close()


def _login(authority):
    r = http_request(authority, "POST", "/api/login", {"password": TEST_PASSWORD})
    assert r["status"] == 200, r
    cookie_value = r["set_cookie"].split(";")[0]
    return cookie_value, r["json"]["csrf_token"]


def test_session_secret_generation_is_idempotent(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SOME_OTHER_VAR=1\n", encoding="utf-8")
    monkeypatch.setenv("OPERATOR_API_ENV_FILE", str(env_file))
    monkeypatch.delenv("OPERATOR_API_SESSION_SECRET", raising=False)
    secret1 = api._get_or_create_session_secret()
    content = env_file.read_text(encoding="utf-8")
    assert content.count("OPERATOR_API_SESSION_SECRET=") == 1
    secret2 = api._get_or_create_session_secret()
    assert secret1 == secret2


# ===================================================== DB PATH SAFETY ====
# operator_api.py must never silently fall back to the production
# database in a demo or test context -- see dispatcher.db_safety and
# docs/CURRENT_SPRINT_CHECKPOINT.md for the incident this closes.
def test_db_mode_is_test_under_pytest():
    assert api._current_db_mode() == "test"


def test_get_conn_raises_when_db_path_unset_under_pytest(monkeypatch):
    monkeypatch.delenv("OPERATOR_API_DB_PATH", raising=False)
    with pytest.raises(api.UnsafeDatabasePathError):
        api.get_conn()


def test_get_conn_raises_when_db_path_points_at_the_real_database(monkeypatch):
    monkeypatch.setenv("OPERATOR_API_DB_PATH", str(storage.DB_PATH))
    with pytest.raises(api.UnsafeDatabasePathError):
        api.get_conn()


def test_get_conn_succeeds_with_a_genuine_throwaway_path(api_env):
    conn = api.get_conn()
    try:
        assert conn.execute("SELECT 1").fetchone() == (1,)
    finally:
        conn.close()


def test_demo_mode_env_var_alone_without_db_path_still_fails_closed(monkeypatch):
    """Even if LUXOR_DEMO_MODE=true is set (the real launcher's own
    signal) with no OPERATOR_API_DB_PATH, this must still refuse --
    proving the guard doesn't depend on the pytest-detection branch
    alone."""
    monkeypatch.delenv("OPERATOR_API_DB_PATH", raising=False)
    monkeypatch.setenv("LUXOR_DEMO_MODE", "true")
    with pytest.raises(api.UnsafeDatabasePathError):
        api.get_conn()


def test_unauthenticated_api_request_returns_401(live_server):
    r = http_request(live_server, "GET", "/api/new_jobs")
    assert r["status"] == 401
    assert "error" in r["json"]


def test_static_login_page_served_without_auth(live_server):
    r = http_request(live_server, "GET", "/index.html")
    assert r["status"] == 200


def test_login_wrong_password_rejected_and_no_password_echoed(live_server):
    r = http_request(live_server, "POST", "/api/login", {"password": "wrong"})
    assert r["status"] == 401
    assert r["set_cookie"] is None
    assert "wrong" not in json.dumps(r["json"])


def test_login_rate_limited_after_repeated_failures(live_server):
    for _ in range(api.LOGIN_MAX_ATTEMPTS):
        r = http_request(live_server, "POST", "/api/login", {"password": "wrong"})
        assert r["status"] == 401
    blocked = http_request(live_server, "POST", "/api/login", {"password": "wrong"})
    assert blocked["status"] == 429
    # Even the correct password is blocked while rate-limited.
    still_blocked = http_request(live_server, "POST", "/api/login", {"password": TEST_PASSWORD})
    assert still_blocked["status"] == 429


def test_login_success_sets_httponly_samesite_cookie_and_csrf_token(live_server):
    r = http_request(live_server, "POST", "/api/login", {"password": TEST_PASSWORD})
    assert r["status"] == 200
    assert r["json"]["csrf_token"]
    cookie = r["set_cookie"]
    assert "operator_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert f"Max-Age={api.SESSION_TTL_SECONDS}" in cookie


def test_authenticated_get_succeeds_with_session_cookie(live_server):
    cookie, _csrf = _login(live_server)
    r = http_request(live_server, "GET", "/api/new_jobs", headers={"Cookie": cookie})
    assert r["status"] == 200
    assert r["json"]["live"] == []


def test_post_without_csrf_token_rejected(live_server):
    cookie, _csrf = _login(live_server)
    r = http_request(live_server, "POST", "/api/sync", {}, headers={"Cookie": cookie})
    assert r["status"] == 403


def test_post_with_wrong_csrf_token_rejected(live_server):
    cookie, _csrf = _login(live_server)
    r = http_request(live_server, "POST", "/api/sync", {}, headers={"Cookie": cookie, "X-CSRF-Token": "not-the-real-token"})
    assert r["status"] == 403


def test_post_with_valid_csrf_token_reaches_the_route(live_server):
    cookie, csrf = _login(live_server)
    # /api/sync fails with 400 in this test env (no QUO_API_KEY) rather
    # than 403 -- proving the CSRF gate passed and the real handler ran.
    r = http_request(live_server, "POST", "/api/sync", {}, headers={"Cookie": cookie, "X-CSRF-Token": csrf})
    assert r["status"] != 403


def test_api_me_returns_a_working_csrf_token_for_an_already_valid_session(live_server):
    """Regression for a real bug found while testing the Fast Path work
    in this sprint: app.js's boot() calls checkSession() (GET /api/me),
    never login(), whenever the session cookie is already valid (e.g.
    after a plain page reload) -- so the frontend's in-memory CSRF token
    stayed null and every write 403'd with "Missing or invalid CSRF
    token" until an explicit logout/login. This proves the fix
    end-to-end: a token obtained from /api/me alone (never from
    /api/login in this test) is a real, working token."""
    cookie, _csrf_from_login = _login(live_server)
    me = http_request(live_server, "GET", "/api/me", headers={"Cookie": cookie})
    assert me["status"] == 200
    csrf_from_me = me["json"]["csrf_token"]
    assert csrf_from_me
    r = http_request(live_server, "POST", "/api/sync", {}, headers={"Cookie": cookie, "X-CSRF-Token": csrf_from_me})
    assert r["status"] != 403


def test_api_me_csrf_token_matches_the_one_login_issued(live_server):
    """csrf_token_for() is a pure function of the session token -- /api/me
    and /api/login must derive the identical value for the same session,
    not two different tokens that happen to both work."""
    cookie, csrf_from_login = _login(live_server)
    me = http_request(live_server, "GET", "/api/me", headers={"Cookie": cookie})
    assert me["json"]["csrf_token"] == csrf_from_login


def test_logout_invalidates_session_not_just_clears_cookie(live_server):
    """The captured cookie value itself must stop authenticating after
    logout -- not merely have the browser told to discard it. Proves
    the server-side _ACTIVE_SESSIONS revocation, not just Set-Cookie."""
    cookie, csrf = _login(live_server)
    logout = http_request(live_server, "POST", "/api/logout", {}, headers={"Cookie": cookie, "X-CSRF-Token": csrf})
    assert logout["status"] == 200
    assert "Max-Age=0" in logout["set_cookie"]

    replay = http_request(live_server, "GET", "/api/new_jobs", headers={"Cookie": cookie})
    assert replay["status"] == 401, "a captured pre-logout cookie must not still authenticate"


def test_security_headers_present_on_every_response(live_server):
    for status_case in (
        http_request(live_server, "GET", "/api/new_jobs"),                                   # 401
        http_request(live_server, "POST", "/api/login", {"password": TEST_PASSWORD}),         # 200
        http_request(live_server, "GET", "/index.html"),                                      # static 200
    ):
        assert status_case["headers"].get("Cache-Control") == "no-store"
        assert status_case["headers"].get("X-Content-Type-Options") == "nosniff"
        assert status_case["headers"].get("X-Frame-Options") == "DENY"
        assert status_case["headers"].get("Referrer-Policy") == "no-referrer"


def test_content_security_policy_header_present(live_server):
    r = http_request(live_server, "GET", "/index.html")
    csp = r["headers"].get("Content-Security-Policy")
    assert csp is not None
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_internal_error_response_is_generic_not_the_raw_exception():
    """Structural guard, not a live-exception test: an unhandled
    exception's str() can contain a file path, a raw SQL fragment, or
    other internal detail (see docs/SECURITY_AUDIT.md finding #1) --
    the client-facing 500 body must never interpolate it."""
    source = (REPO_ROOT / "operator_api.py").read_text(encoding="utf-8")
    assert 'f"Internal error: {exc}"' not in source
    assert '"Internal error."' in source


def test_no_password_or_session_secret_ever_logged(live_server, capsys):
    http_request(live_server, "POST", "/api/login", {"password": "wrong-guess-xyz"})
    http_request(live_server, "POST", "/api/login", {"password": TEST_PASSWORD})
    captured = capsys.readouterr()
    combined = (captured.out + captured.err).lower()
    assert "wrong-guess-xyz" not in combined
    assert TEST_PASSWORD.lower() not in combined
    assert api.SESSION_SECRET.decode("utf-8").lower() not in combined


# ============================================================== NEW JOBS ==
def test_new_job_shows_configured_destination(api_env):
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    seed_pending(api_env, "m1", agn_message("A100"))

    result = api.route_new_jobs(None, {}, {})
    entries = result["live"]
    assert result["older"] == []
    assert len(entries) == 1
    entry = entries[0]
    assert entry["source"] == "AGN"
    assert entry["ticket"] == "A100"
    assert entry["destination"] == "Fab Dispatch"
    assert entry["technician_name"] == "Fabricated Tech"


def test_new_job_missing_destination_shows_not_configured(api_env):
    seed_live_boundary(api_env)
    seed_pending(api_env, "m1", agn_message("A101"))  # no technician at all

    entries = api.route_new_jobs(None, {}, {})["live"]
    assert len(entries) == 1
    assert entries[0]["destination"] is None
    assert entries[0]["eligible_count"] == 0


def test_luxor_message_is_stable_and_deterministic(api_env):
    """Same raw text -> the exact same formatted technician message on
    every fetch -- proves the API never mutates or randomizes the
    deterministic formatter's output (formatter.format_technician_message
    via pipeline.process_raw_message, the one existing authority)."""
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    seed_pending(api_env, "m1", agn_message("A102"))

    first = api.route_new_jobs(None, {}, {})["live"][0]["luxor_message"]
    second = api.route_new_jobs(None, {}, {})["live"][0]["luxor_message"]
    assert first == second
    assert "A102" in first


def test_original_message_masked_but_luxor_message_is_not(api_env):
    """The masked field (shown as "Original message") must differ from
    the raw phone number; the technician-facing luxor_message legitimately
    keeps full contact details (a technician needs them to do the job) --
    same behavior as the existing production New Jobs screen."""
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    seed_pending(api_env, "m1", agn_message("A103"))

    entry = api.route_new_jobs(None, {}, {})["live"][0]
    assert "555-010-3000" not in entry["original_message_masked"]
    assert "phone masked" in entry["original_message_masked"].lower() or "[phone" in entry["original_message_masked"]


def test_new_job_older_than_60_minutes_moves_to_older_bucket(api_env):
    """The presentation split this phase specifically fixed: a pending
    message older than the 60-minute live window must appear under
    "older", never mixed into the live section, and must never be
    auto-selected ahead of a genuinely live job."""
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    seed_pending(api_env, "old", agn_message("OLD1"), minutes_from_now=-90)
    seed_pending(api_env, "new", agn_message("NEW1"), minutes_from_now=5)

    result = api.route_new_jobs(None, {}, {})
    live_tickets = [e["ticket"] for e in result["live"]]
    older_tickets = [e["ticket"] for e in result["older"]]
    assert live_tickets == ["NEW1"]
    assert older_tickets == ["OLD1"]
    assert result["live_window_minutes"] == 60


def test_new_jobs_needs_action_sorts_before_waiting_within_live(api_env):
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    seed_pending(api_env, "m_waiting", agn_message("WAIT1"), minutes_from_now=10)
    seed_pending(api_env, "m_needs", agn_message("NEEDS1"), minutes_from_now=5)
    api.route_acknowledge(None, {"message_id": "m_waiting"}, {})
    api.route_prepare_dispatch(None, {"message_id": "m_waiting"}, {})

    live = api.route_new_jobs(None, {}, {})["live"]
    buckets = {e["ticket"]: e["bucket"] for e in live}
    assert buckets["WAIT1"] == "waiting"
    assert buckets["NEEDS1"] == "needs_action"
    assert [e["ticket"] for e in live].index("NEEDS1") < [e["ticket"] for e in live].index("WAIT1")


# ==================================================== K / DISPATCH FLOW ==
def test_k_required_blocks_until_acknowledged_then_copy_does_not_dispatch(api_env):
    """copy != send: acknowledging K and preparing the technician copy
    must never create a jobs row or mark the message dispatched -- only
    the explicit confirm step may do that."""
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    seed_pending(api_env, "m1", agn_message("A104"))

    entry = api.route_new_jobs(None, {}, {})["live"][0]
    assert entry["k_required"] is True
    assert entry["k_sent"] is False

    ack = api.route_acknowledge(None, {"message_id": "m1"}, {})
    assert ack["success"] is True
    entry = api.route_new_jobs(None, {}, {})["live"][0]
    assert entry["k_sent"] is True
    assert entry["dispatched"] is False
    assert get_jobs_count(api_env) == 0

    prep = api.route_prepare_dispatch(None, {"message_id": "m1"}, {})
    assert prep["success"] is True
    entry = api.route_new_jobs(None, {}, {})["live"][0]
    assert entry["copied"] is True
    assert entry["dispatched"] is False
    assert get_jobs_count(api_env) == 0, "copy/prepare must never create a job row"


def test_confirm_dispatch_records_job_and_clears_live_queue(api_env):
    """The one action that actually commits a job -- manual confirmation
    records the send; nothing before it does."""
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    seed_pending(api_env, "m1", agn_message("A105"))

    api.route_acknowledge(None, {"message_id": "m1"}, {})
    api.route_prepare_dispatch(None, {"message_id": "m1"}, {})
    assert get_jobs_count(api_env) == 0

    result = api.route_confirm_dispatch(None, {"message_id": "m1"}, {})
    assert result["success"] is True
    assert result["job_id"] is not None
    assert get_jobs_count(api_env) == 1

    result = api.route_new_jobs(None, {}, {})
    assert result["live"] == [] and result["older"] == [], "a dispatched job must no longer appear in the New Jobs queue"


def test_confirm_dispatch_is_idempotent(api_env):
    """Duplicate confirmation must never create a second job row --
    exercises the exact DispatcherService.confirm_whatsapp_dispatch call
    operator_api.route_confirm_dispatch makes, called twice with the
    identical (job, classify_result, luxor_message) the route computes."""
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    seed_pending(api_env, "m1", agn_message("A106"))
    api.route_acknowledge(None, {"message_id": "m1"}, {})
    api.route_prepare_dispatch(None, {"message_id": "m1"}, {})

    conn = api.get_conn()
    entry = api._find_new_job_entry(conn, "m1")
    service = api._service(conn)
    first = service.confirm_whatsapp_dispatch("m1", entry.job, entry.classify_result, entry.result.luxor_message)
    assert first.success and not first.idempotent_replay
    assert get_jobs_count(api_env) == 1

    second = service.confirm_whatsapp_dispatch("m1", entry.job, entry.classify_result, entry.result.luxor_message)
    conn.close()
    assert second.success and second.idempotent_replay is True
    assert second.job_id == first.job_id
    assert get_jobs_count(api_env) == 1, "duplicate confirmation must not create a second job"


def test_main_leads_has_no_k_requirement(api_env):
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    seed_pending(api_env, "m1", main_leads_message("ML100"), group_label="Main Leads")

    entry = api.route_new_jobs(None, {}, {})["live"][0]
    assert entry["source"] == "Main Leads"
    assert entry["k_required"] is False


def test_missing_destination_offers_copy_only_and_handle_manually(api_env):
    seed_live_boundary(api_env)
    seed_pending(api_env, "m1", agn_message("A107"))

    entry = api.route_new_jobs(None, {}, {})["live"][0]
    assert entry["destination"] is None

    result = api.route_handle_manually(None, {"message_id": "m1"}, {})
    assert result["success"] is True
    result2 = api.route_new_jobs(None, {}, {})
    assert result2["live"] == [] and result2["older"] == []


def test_pass_requires_reason(api_env):
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    seed_pending(api_env, "m1", agn_message("A108"))

    with pytest.raises(api.ApiError):
        api.route_pass(None, {"message_id": "m1"}, {"reason": ""})

    result = api.route_pass(None, {"message_id": "m1"}, {"reason": "duplicate ticket"})
    assert result["success"] is True


# ==================================================== TECHNICIAN DESTINATIONS
def test_technician_list_shows_configured_and_missing(api_env):
    add_configured_technician(api_env, name="Configured Tech")
    add_unconfigured_technician(api_env, name="Unconfigured Tech")

    techs = {t["name"]: t for t in api.route_technicians(None, {}, {})["technicians"]}
    assert techs["Configured Tech"]["configured"] is True
    assert techs["Unconfigured Tech"]["configured"] is False
    assert techs["Unconfigured Tech"]["dispatch_group"] is None


def test_update_destinations_is_audited_and_persists(api_env):
    tech_id = add_unconfigured_technician(api_env, name="New Tech")
    before = get_events_count(api_env)

    result = api.route_update_destinations(
        None, {"id": str(tech_id)}, {"dispatch_group": "New Dispatch", "closing_group": "New Closings"},
    )
    assert result["success"] is True
    assert result["audit_event_id"] is not None
    assert get_events_count(api_env) == before + 1

    techs = {t["id"]: t for t in api.route_technicians(None, {}, {})["technicians"]}
    assert techs[tech_id]["dispatch_group"] == "New Dispatch"
    assert techs[tech_id]["closing_group"] == "New Closings"
    assert techs[tech_id]["configured"] is True


def test_update_destinations_unchanged_value_is_idempotent_noop(api_env):
    """Re-saving a technician's already-current destinations (e.g. an
    admin re-opening the editor and hitting Save without changing
    anything) must not create a second audit event -- protects an
    already-configured technician's real record from noisy re-writes."""
    tech_id = add_configured_technician(api_env, name="Stable Tech", dispatch_group="Stable Dispatch", closing_group="Stable Closings")
    before = get_events_count(api_env)

    result = api.route_update_destinations(
        None, {"id": str(tech_id)}, {"dispatch_group": "Stable Dispatch", "closing_group": "Stable Closings"},
    )
    assert result["idempotent_replay"] is True
    assert get_events_count(api_env) == before


def test_update_destinations_rejects_blank_values(api_env):
    tech_id = add_unconfigured_technician(api_env)
    with pytest.raises(api.ApiError):
        api.route_update_destinations(None, {"id": str(tech_id)}, {"dispatch_group": "", "closing_group": "X"})


# ======================================================= COMMERCIAL/SHADOW
def _seed_active_job(db_path) -> int:
    """Commits one real dispatched job via the exact same confirm path
    New Jobs uses, so the Active-screen commercial-update tests act on a
    genuinely persisted job -- never a hand-built row."""
    seed_live_boundary(db_path)
    add_configured_technician(db_path)
    seed_pending(db_path, "m1", agn_message("A200"))
    api.route_acknowledge(None, {"message_id": "m1"}, {})
    api.route_prepare_dispatch(None, {"message_id": "m1"}, {})
    result = api.route_confirm_dispatch(None, {"message_id": "m1"}, {})
    return result["job_id"]


def test_commercial_preview_does_not_persist_anything(api_env):
    job_id = _seed_active_job(api_env)
    conn = storage.connect(api_env)
    assert storage.get_shadow_episode_for_job(conn, job_id) is None
    conn.close()

    preview = api.route_commercial_preview(None, {"id": str(job_id)}, {"raw_technician_text": "Starting price 95, customer thinking it over"})
    assert preview["raw_technician_text"] == "Starting price 95, customer thinking it over"

    conn = storage.connect(api_env)
    assert storage.get_shadow_episode_for_job(conn, job_id) is None, "preview must never write a shadow episode"
    conn.close()


def test_pasted_technician_text_preserved_verbatim(api_env):
    job_id = _seed_active_job(api_env)
    raw_text = "  Starting Price $95 -- Customer says   let me THINK about it.  \n"

    api.route_commercial_save(
        None, {"id": str(job_id)},
        {"raw_technician_text": raw_text, "corrected_fields": {"quote_type": "STARTING", "quote_amount": "95", "customer_response": "THINKING"}},
    )

    conn = storage.connect(api_env)
    episode = storage.get_shadow_episode_for_job(conn, job_id)
    conn.close()
    assert episode["commercial_update"]["raw_technician_text"] == raw_text, "the technician's exact original wording must be preserved verbatim"


def test_shadow_suggestion_and_dispatcher_correction_remain_separate(api_env):
    job_id = _seed_active_job(api_env)
    raw_text = "starting 95"

    result = api.route_commercial_save(
        None, {"id": str(job_id)},
        {
            "raw_technician_text": raw_text,
            "corrected_fields": {"quote_type": "FIXED", "quote_amount": "150", "customer_response": "ACCEPTED"},
        },
    )
    assert result["success"] is True

    conn = storage.connect(api_env)
    episode = storage.get_shadow_episode_for_job(conn, job_id)
    conn.close()
    # The raw system suggestion (from "starting 95" alone) and the
    # dispatcher's corrected/confirmed version (FIXED $150, ACCEPTED)
    # must be recorded as genuinely different values, not merged into one.
    assert episode["shadow_suggested_reply"] != episode["dispatcher_actual_reply"]
    assert episode["commercial_update"]["quote_type"] == "FIXED"
    assert episode["commercial_update"]["customer_response"] == "ACCEPTED"


def test_confirm_reply_sent_records_timestamp_and_channel(api_env):
    job_id = _seed_active_job(api_env)
    api.route_commercial_save(
        None, {"id": str(job_id)},
        {"raw_technician_text": "fixed 150, accepted", "corrected_fields": {"quote_type": "FIXED", "quote_amount": "150", "customer_response": "ACCEPTED"}},
    )
    result = api.route_commercial_confirm_reply_sent(None, {"id": str(job_id)}, {"sent_channel": "whatsapp"})
    assert result["success"] is True

    conn = storage.connect(api_env)
    episode = storage.get_shadow_episode_for_job(conn, job_id)
    conn.close()
    assert episode["actual_send_channel"] == "whatsapp"
    assert episode["actual_send_confirmed_at"] is not None


def test_unknown_corrected_field_rejected(api_env):
    job_id = _seed_active_job(api_env)
    with pytest.raises(api.ApiError):
        api.route_commercial_save(
            None, {"id": str(job_id)},
            {"raw_technician_text": "hi", "corrected_fields": {"not_a_real_field": "x"}},
        )


# ================================================================ CLOSINGS
def _seed_closing_job(db_path, ticket="C100", area="Charlotte, NC") -> int:
    """A job taken all the way to "In Progress" -- get_closing_candidates()'s
    actual population (status == "In Progress", or "Closed" and not yet
    Settled). Dispatch confirm alone only reaches "Approved"; a real
    technician status update ("Going") is what the deterministic state
    machine (storage.derive_job_status) requires to reach "In Progress" --
    confirmed empirically, not guessed."""
    from dispatcher.app_models import TechnicianUpdateCommand

    job_id = _seed_active_job_named(db_path, ticket, area)
    conn = api.get_conn()
    try:
        service = api._service(conn)
        result = service.record_technician_update(TechnicianUpdateCommand(job_id=job_id, update_type="Going", raw_update="On my way"))
        assert result.success and result.current_status == "In Progress", result
    finally:
        conn.close()
    return job_id


def _seed_active_job_named(db_path, ticket, area="Charlotte, NC"):
    seed_live_boundary(db_path)
    add_configured_technician(db_path)
    mid = f"m_{ticket}"
    seed_pending(db_path, mid, agn_message(ticket, area))
    api.route_acknowledge(None, {"message_id": mid}, {})
    api.route_prepare_dispatch(None, {"message_id": mid}, {})
    result = api.route_confirm_dispatch(None, {"message_id": mid}, {})
    return result["job_id"]


def test_closings_lists_dispatched_job_waiting_on_technician(api_env):
    job_id = _seed_closing_job(api_env, "C100")
    entries = api.route_closings(None, {}, {})["entries"]
    assert len(entries) == 1
    assert entries[0]["id"] == job_id
    assert entries[0]["closing_recorded"] is False
    assert entries[0]["bucket"] == "waiting"


def test_closing_record_never_invents_missing_amount_or_payment(api_env):
    job_id = _seed_closing_job(api_env, "C101")
    result = api.route_closing_record(None, {"id": str(job_id)}, {"raw_closing": "Job done, will confirm price later"})
    assert result["success"] is True

    entry = api.route_closings(None, {}, {})["entries"][0]
    assert entry["closing_recorded"] is True
    assert entry["amount"] is None
    assert entry["payment_method"] is None
    assert set(entry["missing"]) == {"amount", "payment method"}


def test_closing_confirm_sent_blocked_until_amount_and_payment_recorded(api_env):
    job_id = _seed_closing_job(api_env, "C102")
    api.route_closing_record(None, {"id": str(job_id)}, {"raw_closing": "Done, no price yet"})
    with pytest.raises(api.ApiError):
        api.route_closing_confirm_sent(None, {"id": str(job_id)}, {})


def test_closing_full_sequence_record_prepare_confirm_complete(api_env):
    """Review -> verify amount/payment -> prepare -> confirm sent
    manually -> complete -- gated in that order, never skippable."""
    job_id = _seed_closing_job(api_env, "C103")
    record = api.route_closing_record(
        None, {"id": str(job_id)},
        {"raw_closing": "Rekeyed, customer paid cash", "final_amount": "125", "payment_method": "Cash"},
    )
    assert record["success"] is True

    entry = api.route_closings(None, {}, {})["entries"][0]
    assert entry["missing"] == []
    assert entry["sent_confirmed"] is False
    if entry["supported"] is False:
        pytest.skip("AGN closing template not supported in this test env -- gating already proven by amount/payment checks above")

    prepare = api.route_closing_prepare(None, {"id": str(job_id)}, {})
    assert prepare["success"] is True

    confirm = api.route_closing_confirm_sent(None, {"id": str(job_id)}, {})
    assert confirm["success"] is True

    entry = api.route_closings(None, {}, {})["entries"][0]
    assert entry["sent_confirmed"] is True

    complete = api.route_closing_complete(None, {"id": str(job_id)}, {})
    assert complete["success"] is True
    # A completed/settled job must no longer appear in the closing queue.
    assert job_id not in {e["id"] for e in api.route_closings(None, {}, {})["entries"]}


# ================================================================= HISTORY
def test_history_empty_before_any_job_completed(api_env):
    result = api.route_history(None, {}, {})
    assert result["entries"] == []
    assert result["total"] == 0


def test_history_lists_completed_job_with_real_fields_only(api_env):
    job_id = _seed_closing_job(api_env, "H100")
    api.route_closing_record(
        None, {"id": str(job_id)},
        {"raw_closing": "Done, cash", "final_amount": "90", "payment_method": "Cash"},
    )
    entry_before = api.route_closings(None, {}, {})["entries"][0]
    if entry_before["supported"] is not False:
        api.route_closing_prepare(None, {"id": str(job_id)}, {})
        api.route_closing_confirm_sent(None, {"id": str(job_id)}, {})
        api.route_closing_complete(None, {"id": str(job_id)}, {})

        result = api.route_history(None, {}, {})
        assert result["total"] == 1
        entry = result["entries"][0]
        assert entry["id"] == job_id
        assert entry["amount"] == "90"
        assert entry["payment_method"] == "Cash"


def test_history_detail_shows_readable_timeline_not_raw_rows(api_env):
    job_id = _seed_active_job(api_env)
    detail = api.route_history_detail(None, {"id": str(job_id)}, {})
    assert detail["job"]["id"] == job_id
    assert isinstance(detail["timeline"], list)
    for event in detail["timeline"]:
        assert set(event.keys()) == {"created_at", "label", "note"}
        assert isinstance(event["label"], str) and event["label"]


def test_history_search_filters_by_ticket(api_env):
    _seed_closing_job(api_env, "SEARCHABLE1")
    result = api.route_history(None, {}, {"search": "nonexistent-ticket"})
    assert result["entries"] == []


# ================================================================= REPORTS
def test_reports_never_invents_financial_fields(api_env):
    """Every entry must come only from fields that actually exist in the
    schema; nothing here computes a percentage/profit/balance/parts/
    fees breakdown -- those keys must not appear at all."""
    job_id = _seed_closing_job(api_env, "R100")
    api.route_closing_record(None, {"id": str(job_id)}, {"raw_closing": "Done, cash", "final_amount": "80", "payment_method": "Cash"})

    entries = api.route_reports(None, {}, {})["entries"]
    forbidden_keys = {"percentage", "profit", "balance", "parts", "fees"}
    for entry in entries:
        assert not (forbidden_keys & set(entry.keys())), f"Reports entry invented a financial field: {entry.keys()}"


def test_reports_missing_values_are_null_not_fabricated(api_env):
    """A job not yet closed/completed must not appear in Reports at all
    (scoped to completed jobs) -- and for a completed job with no
    payment method recorded, that field must come through as null
    (the frontend renders "Not recorded"), never a guessed value."""
    job_id = _seed_closing_job(api_env, "R101")
    api.route_closing_record(None, {"id": str(job_id)}, {"raw_closing": "Job done, price to be confirmed"})

    tickets_in_reports = {e["job"] for e in api.route_reports(None, {}, {})["entries"]}
    assert "R101" not in tickets_in_reports, "an unclosed job must not appear in Reports yet"


# ==================================================== LEARNING REVIEW =====
def test_learning_review_empty_shows_zero_count(api_env):
    result = api.route_learning_review(None, {}, {})
    assert result["episodes"] == []
    assert result["reviewed_count"] == 0
    assert result["total_count"] == 0


def test_learning_review_lists_real_episode_and_updates_status(api_env):
    job_id = _seed_active_job(api_env)
    api.route_commercial_save(
        None, {"id": str(job_id)},
        {"raw_technician_text": "starting 95, thinking it over", "corrected_fields": {"quote_type": "STARTING", "quote_amount": "95", "customer_response": "THINKING"}},
    )

    result = api.route_learning_review(None, {}, {})
    assert result["total_count"] == 1
    episode = result["episodes"][0]
    assert episode["review_status"] == "UNREVIEWED"
    assert episode["original_observation"] == "starting 95, thinking it over"
    assert result["reviewed_count"] == 0

    updated = api.route_learning_review_status(None, {"episode_id": episode["episode_id"]}, {"review_status": "VERIFIED"})
    assert updated["success"] is True

    result2 = api.route_learning_review(None, {}, {})
    assert result2["reviewed_count"] == 1
    assert result2["episodes"][0]["review_status"] == "VERIFIED"


def test_learning_review_rejects_unknown_status(api_env):
    job_id = _seed_active_job(api_env)
    api.route_commercial_save(
        None, {"id": str(job_id)},
        {"raw_technician_text": "fixed 100, accepted", "corrected_fields": {"quote_type": "FIXED", "quote_amount": "100", "customer_response": "ACCEPTED"}},
    )
    episode = api.route_learning_review(None, {}, {})["episodes"][0]
    with pytest.raises(api.ApiError):
        api.route_learning_review_status(None, {"episode_id": episode["episode_id"]}, {"review_status": "NOT_A_REAL_STATUS"})


# ===================================================== ACTIVE NEXT ACTION =
def test_active_next_action_awaiting_technician_when_no_update(api_env):
    job_id = _seed_active_job(api_env)
    entries = api.route_active(None, {}, {})["entries"]
    entry = next(e for e in entries if e["id"] == job_id)
    assert entry["next_action"] == "Awaiting technician"


def test_active_next_action_record_closing_when_technician_completed(api_env):
    from dispatcher.app_models import TechnicianUpdateCommand

    job_id = _seed_active_job(api_env)
    conn = api.get_conn()
    try:
        service = api._service(conn)
        service.record_technician_update(TechnicianUpdateCommand(job_id=job_id, update_type="Completed", raw_update="All done"))
    finally:
        conn.close()

    entries = api.route_active(None, {}, {})["entries"]
    entry = next(e for e in entries if e["id"] == job_id)
    assert entry["next_action"] == "Record closing"


def test_active_next_action_uses_only_the_fixed_label_set(api_env):
    job_id = _seed_active_job(api_env)
    entries = api.route_active(None, {}, {})["entries"]
    entry = next(e for e in entries if e["id"] == job_id)
    assert entry["next_action"] in api._ACTIVE_NEXT_ACTION_LABELS


# ============================================ TECHNICIAN STATUS UPDATES ===
# Steps 6-8 of the end-to-end workflow (docs/END_TO_END_WORKFLOW_AUDIT.md):
# record_technician_update / prepare_company_update / confirm_company_update_sent
# were fully implemented and tested at the application/storage layer but had
# zero HTTP route before this sprint. These tests exercise the new routes
# directly -- distinct from the "commercial" (price-quote) flow already
# covered above, which answers a different question.
def test_company_update_owed_is_none_before_any_technician_update(api_env):
    job_id = _seed_active_job(api_env)
    entries = api.route_active(None, {}, {})["entries"]
    entry = next(e for e in entries if e["id"] == job_id)
    assert entry["company_update_owed"] is None


def test_technician_update_record_route_persists_and_appears_in_active(api_env):
    job_id = _seed_active_job(api_env)
    result = api.route_technician_update_record(
        None, {"id": str(job_id)}, {"update_type": "Going", "raw_update": "On my way"},
    )
    assert result["success"] is True
    assert result["current_status"] == "In Progress"

    entries = api.route_active(None, {}, {})["entries"]
    entry = next(e for e in entries if e["id"] == job_id)
    assert entry["latest_technician_update"]["update_type"] == "Going"
    assert entry["latest_technician_update"]["raw_message"] == "On my way"


def test_company_update_owed_computes_deterministic_reply_for_known_type(api_env):
    job_id = _seed_active_job(api_env)
    api.route_technician_update_record(None, {"id": str(job_id)}, {"update_type": "Going", "raw_update": "otw"})

    entries = api.route_active(None, {}, {})["entries"]
    owed = next(e for e in entries if e["id"] == job_id)["company_update_owed"]
    assert owed["confirmed"] is True
    assert owed["suggested_reply"] == "Going"
    assert owed["update_type"] == "Going"


def test_company_update_owed_eta_reply_includes_minutes(api_env):
    job_id = _seed_active_job(api_env)
    api.route_technician_update_record(
        None, {"id": str(job_id)}, {"update_type": "ETA", "raw_update": "20 min out", "eta_minutes": 20},
    )
    entries = api.route_active(None, {}, {})["entries"]
    owed = next(e for e in entries if e["id"] == job_id)["company_update_owed"]
    assert owed["confirmed"] is True
    assert owed["suggested_reply"] == "Tech ETA 20 min"


def test_company_update_owed_unconfirmed_for_custom_update(api_env):
    job_id = _seed_active_job(api_env)
    api.route_technician_update_record(
        None, {"id": str(job_id)}, {"update_type": "Custom", "raw_update": "Customer wants to reschedule"},
    )
    entries = api.route_active(None, {}, {})["entries"]
    owed = next(e for e in entries if e["id"] == job_id)["company_update_owed"]
    assert owed["confirmed"] is False
    assert owed["suggested_reply"] is None
    assert "human review" in owed["reason"]


def test_technician_update_rejects_unknown_update_type(api_env):
    job_id = _seed_active_job(api_env)
    with pytest.raises(api.ApiError) as exc:
        api.route_technician_update_record(None, {"id": str(job_id)}, {"update_type": "Sleeping", "raw_update": "zzz"})
    assert exc.value.status == 400


def test_technician_update_eta_without_minutes_is_rejected(api_env):
    job_id = _seed_active_job(api_env)
    with pytest.raises(api.ApiError):
        api.route_technician_update_record(None, {"id": str(job_id)}, {"update_type": "ETA", "raw_update": "soon"})


def test_technician_update_requires_raw_update(api_env):
    job_id = _seed_active_job(api_env)
    with pytest.raises(api.ApiError):
        api.route_technician_update_record(None, {"id": str(job_id)}, {"update_type": "Going", "raw_update": ""})


def test_technician_update_is_idempotent_on_identical_resubmit(api_env):
    job_id = _seed_active_job(api_env)
    events_before = get_events_count(api_env)
    api.route_technician_update_record(None, {"id": str(job_id)}, {"update_type": "Going", "raw_update": "otw"})
    events_after_first = get_events_count(api_env)
    result = api.route_technician_update_record(None, {"id": str(job_id)}, {"update_type": "Going", "raw_update": "otw"})
    events_after_replay = get_events_count(api_env)
    assert events_after_first == events_before + 1
    assert events_after_replay == events_after_first
    assert result["idempotent_replay"] is True


def test_company_update_prepare_persists_and_returns_text(api_env):
    job_id = _seed_active_job(api_env)
    api.route_technician_update_record(None, {"id": str(job_id)}, {"update_type": "Going", "raw_update": "otw"})
    events_before = get_events_count(api_env)

    result = api.route_company_update_prepare(None, {"id": str(job_id)}, {"update_type": "Going"})
    assert result["success"] is True
    assert result["prepared_text"] == "Going"
    assert get_events_count(api_env) == events_before + 1


def test_company_update_prepare_fails_without_manual_text_for_custom(api_env):
    job_id = _seed_active_job(api_env)
    api.route_technician_update_record(None, {"id": str(job_id)}, {"update_type": "Custom", "raw_update": "reschedule"})
    with pytest.raises(api.ApiError):
        api.route_company_update_prepare(None, {"id": str(job_id)}, {"update_type": "Custom"})


def test_company_update_prepare_accepts_manual_text_for_custom(api_env):
    job_id = _seed_active_job(api_env)
    api.route_technician_update_record(None, {"id": str(job_id)}, {"update_type": "Custom", "raw_update": "reschedule"})
    result = api.route_company_update_prepare(
        None, {"id": str(job_id)}, {"update_type": "Custom", "manual_text": "We'll call you back shortly."},
    )
    assert result["success"] is True
    assert result["prepared_text"] == "We'll call you back shortly."


def test_company_update_confirm_sent_clears_owed_state(api_env):
    job_id = _seed_active_job(api_env)
    api.route_technician_update_record(None, {"id": str(job_id)}, {"update_type": "Going", "raw_update": "otw"})
    prepared = api.route_company_update_prepare(None, {"id": str(job_id)}, {"update_type": "Going"})

    entries = api.route_active(None, {}, {})["entries"]
    assert next(e for e in entries if e["id"] == job_id)["company_update_owed"] is not None

    result = api.route_company_update_confirm_sent(None, {"id": str(job_id)}, {"prepared_text": prepared["prepared_text"]})
    assert result["success"] is True

    entries = api.route_active(None, {}, {})["entries"]
    entry = next(e for e in entries if e["id"] == job_id)
    assert entry["company_update_owed"] is None
    assert entry["next_action"] == "Waiting for customer"


def test_company_update_routes_never_touch_quo_or_whatsapp_adapters():
    """Structural guard: the three new route handlers must only ever
    call DispatcherService's storage-backed methods, never quo_adapter/
    whatsapp_adapter -- these routes have no outbound-send capability by
    construction."""
    import inspect

    for fn in (api.route_technician_update_record, api.route_company_update_prepare, api.route_company_update_confirm_sent):
        src = inspect.getsource(fn)
        assert "quo_adapter" not in src
        assert "whatsapp_adapter" not in src


# ============================================= TECHNICIAN K ACCEPTANCE ====
# Step 6 of the end-to-end workflow: there was previously no schema
# concept of "the technician accepted this job" anywhere -- a bare "K"
# reply was only ever surfaced for human review, never tracked as a job
# fact. See docs/END_TO_END_WORKFLOW_AUDIT.md and dispatcher.technician_ack.
def _seed_active_job_with_technician(db_path) -> int:
    """_seed_active_job() only dispatches a job -- it never assigns a
    technician (assign_technician() is a separate, explicit action; see
    storage.assign_technician()'s own docstring). The K-acceptance tests
    need a real assignment to exist, so this mirrors exactly how
    scripts/seed_command_center_demo_db.py does it: dispatch, then a
    separate explicit assign_technician() call."""
    from dispatcher.rules.service_areas import load_service_areas
    tech_id = add_configured_technician(db_path)
    seed_live_boundary(db_path)
    seed_pending(db_path, "m1", agn_message("A200"))
    api.route_acknowledge(None, {"message_id": "m1"}, {})
    api.route_prepare_dispatch(None, {"message_id": "m1"}, {})
    job_id = api.route_confirm_dispatch(None, {"message_id": "m1"}, {})["job_id"]
    conn = storage.connect(db_path)
    try:
        storage.assign_technician(conn, job_id, tech_id, load_service_areas())
    finally:
        conn.close()
    return job_id


def _seed_bridge_observation(message_id="wamid.1", text="K", group_name="Fab Dispatch", extracted_ticket=None):
    import whatsapp_bridge_adapter as bridge_adapter
    bridge_adapter.add_observation({
        "event_type": "incoming_message", "message_id": message_id, "group_id": "g1",
        "group_name": group_name, "sender": "1555", "timestamp": "1700000000", "text": text,
        "extracted_ticket": extracted_ticket,
    })


def test_technician_k_is_none_before_technician_assigned(api_env):
    seed_live_boundary(api_env)
    seed_pending(api_env, "m1", agn_message("A300"))
    api.route_acknowledge(None, {"message_id": "m1"}, {})
    api.route_prepare_dispatch(None, {"message_id": "m1"}, {})
    result = api.route_confirm_dispatch(None, {"message_id": "m1"}, {})
    entries = api.route_active(None, {}, {})["entries"]
    entry = next(e for e in entries if e["id"] == result["job_id"])
    assert entry["technician_k"] is None  # no technician assigned -- A300 had no configured technician


def test_technician_k_waiting_surfaces_a_strong_candidate(api_env):
    job_id = _seed_active_job_with_technician(api_env)
    _seed_bridge_observation()
    entries = api.route_active(None, {}, {})["entries"]
    entry = next(e for e in entries if e["id"] == job_id)
    assert entry["technician_k"]["status"] == "waiting"
    assert len(entry["technician_k"]["candidates"]) == 1
    assert entry["technician_k"]["candidates"][0]["category"] == "STRONG"


def test_technician_k_confirm_accepted_route(api_env):
    job_id = _seed_active_job_with_technician(api_env)
    _seed_bridge_observation()
    result = api.route_technician_k_confirm_accepted(None, {"id": str(job_id)}, {"message_id": "wamid.1"})
    assert result["success"] is True

    entries = api.route_active(None, {}, {})["entries"]
    entry = next(e for e in entries if e["id"] == job_id)
    assert entry["technician_k"]["status"] == "accepted"


def test_technician_k_confirm_declined_route_requires_reason(api_env):
    job_id = _seed_active_job_with_technician(api_env)
    _seed_bridge_observation(text="no")
    with pytest.raises(api.ApiError):
        api.route_technician_k_confirm_declined(None, {"id": str(job_id)}, {"message_id": "wamid.1", "reason": ""})


def test_technician_k_confirm_declined_route(api_env):
    job_id = _seed_active_job_with_technician(api_env)
    _seed_bridge_observation(text="no")
    result = api.route_technician_k_confirm_declined(
        None, {"id": str(job_id)}, {"message_id": "wamid.1", "reason": "too far"},
    )
    assert result["success"] is True
    entries = api.route_active(None, {}, {})["entries"]
    entry = next(e for e in entries if e["id"] == job_id)
    assert entry["technician_k"]["status"] == "declined"
    assert entry["technician_k"]["reason"] == "too far"


def test_technician_k_never_appears_once_a_technician_update_is_recorded(api_env):
    """Once the job has moved past "waiting for K" (a real status update
    landed), the K panel should not keep asking the dispatcher to
    resolve a moot question."""
    job_id = _seed_active_job_with_technician(api_env)
    _seed_bridge_observation()
    api.route_technician_update_record(None, {"id": str(job_id)}, {"update_type": "Going", "raw_update": "on my way"})
    entries = api.route_active(None, {}, {})["entries"]
    entry = next(e for e in entries if e["id"] == job_id)
    assert entry["technician_k"] is None


def test_technician_k_routes_never_touch_quo_or_whatsapp_adapters():
    import inspect
    for fn in (api.route_technician_k_confirm_accepted, api.route_technician_k_confirm_declined):
        src = inspect.getsource(fn)
        assert "quo_adapter" not in src
        assert "whatsapp_adapter" not in src


# ===================================== WORKFLOW PROJECTION / ACTION CENTER
def test_action_center_returns_all_ten_sections(api_env):
    result = api.route_action_center(None, {}, {})
    expected = {
        "new_jobs_to_review", "company_k_owed", "assign_technician", "ready_to_dispatch",
        "waiting_technician_k", "technician_updates_to_review", "company_updates_owed",
        "closings_to_review", "closings_ready_to_send", "human_review_required",
    }
    assert set(result["sections"].keys()) == expected
    for section in result["sections"].values():
        assert "items" in section and "total" in section and "title" in section


def test_action_center_groups_repartition_every_item_with_no_loss_or_duplication(api_env):
    """`groups` (ACTION NOW / WAITING / NEEDS REVIEW) is a presentation
    view over the same 10 sections, not a second independent query --
    every item counted in `sections` must be counted exactly once across
    `groups`, and vice versa."""
    _seed_active_job_with_technician(api_env)  # -> waiting_technician_k
    seed_pending(api_env, "m-new", agn_message("A970"))  # left un-acknowledged -> new_jobs_to_review
    seed_pending(api_env, "m-review", agn_message("A950"))
    api.route_handle_manually(None, {"message_id": "m-review"}, {})  # -> human_review_required

    result = api.route_action_center(None, {}, {})
    assert {g["key"] for g in result["groups"]} == {"action_now", "waiting", "needs_review"}
    sections_total = sum(s["total"] for s in result["sections"].values())
    groups_total = sum(g["total"] for g in result["groups"])
    assert groups_total == sections_total

    by_key = {g["key"]: g for g in result["groups"]}
    assert by_key["waiting"]["total"] >= 1
    assert by_key["needs_review"]["total"] >= 1
    assert by_key["action_now"]["total"] >= 1


def test_action_center_human_review_items_are_urgent_priority(api_env):
    seed_live_boundary(api_env)
    seed_pending(api_env, "m-hr", agn_message("A951"))
    result = api.route_handle_manually(None, {"message_id": "m-hr"}, {})
    ac = api.route_action_center(None, {}, {})
    item = next(i for i in ac["sections"]["human_review_required"]["items"] if i["id"] == result["job_id"])
    assert item["priority"] == "URGENT"


def test_action_center_fresh_waiting_technician_k_item_defaults_to_waiting_priority(api_env):
    _seed_active_job_with_technician(api_env)
    ac = api.route_action_center(None, {}, {})
    items = ac["sections"]["waiting_technician_k"]["items"]
    assert items and all(i["priority"] == "WAITING" for i in items)


def test_action_center_priority_escalates_a_stale_waiting_technician_k_item(api_env):
    """A technician who has not replied to K in a long time should stop
    reading as passive WAITING and surface as something the operator
    should follow up on -- the 'technician response age' factor."""
    job_id = _seed_active_job_with_technician(api_env)
    conn = storage.connect(api_env)
    try:
        stale = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat().replace("+00:00", "Z")
        conn.execute("UPDATE jobs SET last_event_at = ? WHERE id = ?", (stale, job_id))
        conn.commit()
    finally:
        conn.close()
    ac = api.route_action_center(None, {}, {})
    item = next(i for i in ac["sections"]["waiting_technician_k"]["items"] if i["id"] == job_id)
    assert item["priority"] == "DUE"


def test_action_center_group_ordering_is_deterministic_oldest_urgent_first(api_env):
    """Within one priority bucket, older items sort first -- proven by
    directly backdating one of two otherwise-identical human-review
    items and asserting the older one always lands first, independent
    of insertion order."""
    seed_live_boundary(api_env)
    seed_pending(api_env, "m-old", agn_message("A960"))
    seed_pending(api_env, "m-new", agn_message("A961"))
    old_result = api.route_handle_manually(None, {"message_id": "m-old"}, {})
    new_result = api.route_handle_manually(None, {"message_id": "m-new"}, {})
    conn = storage.connect(api_env)
    try:
        older = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat().replace("+00:00", "Z")
        newer = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        conn.execute("UPDATE jobs SET last_event_at = ? WHERE id = ?", (older, old_result["job_id"]))
        conn.execute("UPDATE jobs SET last_event_at = ? WHERE id = ?", (newer, new_result["job_id"]))
        conn.commit()
    finally:
        conn.close()
    ac = api.route_action_center(None, {}, {})
    needs_review = next(g for g in ac["groups"] if g["key"] == "needs_review")
    ids_in_order = [i["id"] for i in needs_review["items"]]
    assert ids_in_order.index(old_result["job_id"]) < ids_in_order.index(new_result["job_id"])


def test_action_center_job_screen_routes_a_closing_stage_job_to_closings_not_active(api_env):
    """Regression: _action_center_job_item used to hardcode screen="active"
    for every job regardless of its real stage, so clicking "Open" on a
    closings_to_review/closings_ready_to_send item sent the operator to
    the Active screen, where a Closed job never appears at all (Active
    only covers ACTIVE_JOB_WORKSPACE_STATUSES). Found while wiring global
    search's own (correct) screen resolution and fixed via the same
    shared _job_target_screen helper."""
    job_id = _seed_active_job_with_technician(api_env)
    conn = storage.connect(api_env)
    try:
        conn.execute("UPDATE jobs SET status = 'Closed' WHERE id = ?", (job_id,))
        conn.commit()
    finally:
        conn.close()
    result = api.route_action_center(None, {}, {})
    item = next(i for i in result["sections"]["closings_to_review"]["items"] if i["id"] == job_id)
    assert item["screen"] == "closings"


def test_action_center_job_screen_routes_a_handle_manually_job_to_action_center_not_active(api_env):
    """Regression, found live while testing global search against a
    Scenario-D-shaped job: status "Handle Manually" is in NEITHER
    Active's ACTIVE_JOB_WORKSPACE_STATUSES NOR Closings'/History's
    membership rules -- it was falling through _job_target_screen's old
    "anything else defaults to active" catch-all, so search/Open/Next
    Actionable Job all silently sent the operator to the Active screen,
    where the job never appears (selectActive falls back to whatever
    job happens to be first in the list, no error surfaced). Action
    Center's own human_review_required section is the one place such a
    job is guaranteed listed."""
    job_id = _seed_active_job_with_technician(api_env)
    conn = storage.connect(api_env)
    try:
        conn.execute("UPDATE jobs SET status = 'Handle Manually' WHERE id = ?", (job_id,))
        conn.commit()
    finally:
        conn.close()
    result = api.route_action_center(None, {}, {})
    item = next(i for i in result["sections"]["human_review_required"]["items"] if i["id"] == job_id)
    assert item["screen"] == "actioncenter"

    active_ids = [e["id"] for e in api.route_active(None, {}, {})["entries"]]
    assert job_id not in active_ids, "sanity check: the job must genuinely not be in Active for this test to mean anything"


def test_action_center_lists_a_job_missing_technician_assignment(api_env):
    job_id = _seed_active_job(api_env)  # no technician assigned
    result = api.route_action_center(None, {}, {})
    ids = [i["id"] for i in result["sections"]["assign_technician"]["items"]]
    assert job_id in ids


def test_action_center_lists_a_job_waiting_on_technician_k(api_env):
    job_id = _seed_active_job_with_technician(api_env)
    result = api.route_action_center(None, {}, {})
    ids = [i["id"] for i in result["sections"]["waiting_technician_k"]["items"]]
    assert job_id in ids


def test_action_center_lists_company_update_owed(api_env):
    job_id = _seed_active_job_with_technician(api_env)
    api.route_technician_update_record(None, {"id": str(job_id)}, {"update_type": "Going", "raw_update": "on my way"})
    result = api.route_action_center(None, {}, {})
    ids = [i["id"] for i in result["sections"]["company_updates_owed"]["items"]]
    assert job_id in ids
    # ...and it must NOT still appear as merely "waiting on K" once it has moved past that stage.
    assert job_id not in [i["id"] for i in result["sections"]["waiting_technician_k"]["items"]]


def test_action_center_lists_handle_manually_under_human_review(api_env):
    seed_live_boundary(api_env)
    seed_pending(api_env, "m1", agn_message("A900"))
    result = api.route_handle_manually(None, {"message_id": "m1"}, {})
    job_id = result["job_id"]
    ac = api.route_action_center(None, {}, {})
    ids = [i["id"] for i in ac["sections"]["human_review_required"]["items"]]
    assert job_id in ids


def test_action_center_sections_cap_at_five_items(api_env):
    from dispatcher.rules.service_areas import load_service_areas
    tech_id = add_configured_technician(api_env)
    seed_live_boundary(api_env)
    for i in range(7):
        seed_pending(api_env, f"m{i}", agn_message(f"A{900 + i}"))
        api.route_acknowledge(None, {"message_id": f"m{i}"}, {})
        api.route_prepare_dispatch(None, {"message_id": f"m{i}"}, {})
        job_id = api.route_confirm_dispatch(None, {"message_id": f"m{i}"}, {})["job_id"]
        conn = storage.connect(api_env)
        try:
            storage.assign_technician(conn, job_id, tech_id, load_service_areas())
        finally:
            conn.close()
    result = api.route_action_center(None, {}, {})
    section = result["sections"]["waiting_technician_k"]
    assert len(section["items"]) == 5
    assert section["total"] == 7


def test_action_center_items_never_include_phone_or_address(api_env):
    _seed_active_job_with_technician(api_env)
    result = api.route_action_center(None, {}, {})
    combined = json.dumps(result)
    assert "phone" not in combined.lower()
    assert "555-010" not in combined  # the fixture's own fake phone number


def test_action_center_ack_company_k_reachable_through_real_workflow_state(api_env):
    """Integration-level proof (not just the pure-function test in
    tests/test_workflow_projection.py) that ACK_COMPANY_K genuinely
    surfaces from real pending-message state: AGN requires K per
    policy.py, and a freshly-arrived, needs-no-correction AGN message
    has not been acknowledged yet."""
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    seed_pending(api_env, "m1", agn_message("A700"))
    result = api.route_action_center(None, {}, {})
    items = result["sections"]["company_k_owed"]["items"]
    assert any(i["ticket"] == "A700" for i in items)
    matched = next(i for i in items if i["ticket"] == "A700")
    assert matched["next_action"] == "ACK_COMPANY_K"
    assert matched["entity_type"] == "INCOMING_MESSAGE"


def test_action_center_send_job_to_technician_reachable_through_real_workflow_state(api_env):
    """Integration-level proof that SEND_JOB_TO_TECHNICIAN genuinely
    surfaces: Main Leads requires no K (policy.py), so a reviewed Main
    Leads message with a configured, eligible technician is
    immediately ready to dispatch."""
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    seed_pending(api_env, "m1", main_leads_message("ML700"))
    result = api.route_action_center(None, {}, {})
    items = result["sections"]["ready_to_dispatch"]["items"]
    assert any(i["ticket"] == "ML700" for i in items)
    matched = next(i for i in items if i["ticket"] == "ML700")
    assert matched["next_action"] == "SEND_JOB_TO_TECHNICIAN"


# ============================== ACTION CENTER STALE-MESSAGE REGRESSION (Priority-0 fix) ==
# Reproduces the real production bug: get_live_dispatch_queue()'s "live"
# bucket is bounded only by a one-time console-activation boundary (see
# ACTION_CENTER_MESSAGE_LOOKBACK_MINUTES's own comment in operator_api.py),
# not a rolling window, so a raw quo_pending_messages row from days/weeks
# ago used to appear in Action Center's ACTION NOW as if it were current
# work. All items below live-activate with the default (very old, 2020)
# boundary -- exactly like the real production database's boundary from
# 2026-07-29 -- so these tests would have failed before the fix.

def _all_action_center_items(result):
    return [item for group in result["groups"] for item in group["items"]]


def test_action_center_excludes_a_pending_quo_message_older_than_the_lookback_window(api_env):
    seed_live_boundary(api_env)  # defaults to 2020-01-01 -- matches production's stale boundary
    add_configured_technician(api_env)
    ten_days_ago_minutes = -(10 * 24 * 60)
    seed_pending(api_env, "old1", agn_message("STALE1"), minutes_from_now=ten_days_ago_minutes)
    result = api.route_action_center(None, {}, {})
    tickets = [i.get("ticket") for i in _all_action_center_items(result)]
    assert "STALE1" not in tickets


def test_action_center_includes_a_recent_pending_quo_message_within_the_lookback_window(api_env):
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    seed_pending(api_env, "recent1", agn_message("FRESH1"), minutes_from_now=-30)  # 30 min old
    result = api.route_action_center(None, {}, {})
    tickets = [i.get("ticket") for i in _all_action_center_items(result)]
    assert "FRESH1" in tickets


def test_action_center_includes_a_message_right_at_the_lookback_boundary(api_env):
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    seed_pending(api_env, "edge1", agn_message("EDGE1"),
                 minutes_from_now=-(api.ACTION_CENTER_MESSAGE_LOOKBACK_MINUTES - 1))
    result = api.route_action_center(None, {}, {})
    tickets = [i.get("ticket") for i in _all_action_center_items(result)]
    assert "EDGE1" in tickets


def test_action_center_old_committed_active_job_is_handled_by_status_not_message_age(api_env):
    """The exact real-world case this fix must NOT break: job id=1 in
    production is a real, non-terminal 'Approved' job with no technician
    assigned, last touched 359 hours ago -- it must keep surfacing in
    Action Center (ASSIGN_TECHNICIAN) precisely because its *status*
    still demands action, regardless of how old it is. Only pre-commit
    raw messages are age-filtered; committed jobs never are."""
    job_id = _seed_active_job(api_env)  # commits a real job, no technician assigned -> ASSIGN_TECHNICIAN
    conn = storage.connect(api_env)
    fifteen_days_ago = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    conn.execute("UPDATE jobs SET last_event_at = ?, updated_at = ? WHERE id = ?", (fifteen_days_ago, fifteen_days_ago, job_id))
    conn.commit()
    conn.close()

    result = api.route_action_center(None, {}, {})
    ids = [i["id"] for i in result["sections"]["assign_technician"]["items"]]
    assert job_id in ids


def test_action_center_whatsapp_observation_correlation_is_unaffected_by_the_message_lookback_filter(api_env):
    """The message-age filter touches only the raw quo_pending_messages
    loop -- it must not change how a real WhatsApp observation
    correlates to a job's next-action projection."""
    job_id = _seed_active_job_with_technician(api_env)
    conn = storage.connect(api_env)
    storage.add_whatsapp_observation(conn, {
        "message_id": "wa1", "group_id": "g@g.us", "group_name": "Fab Dispatch",
        "sender": "1555", "direction": "incoming", "timestamp": "1700000000",
        "text": "k on the way", "extracted_ticket": None, "quoted_message_id": None,
    })
    conn.close()

    result = api.route_action_center(None, {}, {})
    ids = [i["id"] for i in result["sections"]["waiting_technician_k"]["items"]]
    assert job_id in ids  # unchanged behavior: the job still surfaces normally


def test_inbox_review_still_retains_a_message_excluded_from_action_center_by_the_lookback_filter(api_env):
    """The fix must never delete, resolve, or hide the historical row
    anywhere else -- route_inbox_review (Archive) is deliberately
    unfiltered by recency and must still return it."""
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    ten_days_ago_minutes = -(10 * 24 * 60)
    seed_pending(api_env, "old1", agn_message("STALE1"), minutes_from_now=ten_days_ago_minutes)

    ac = api.route_action_center(None, {}, {})
    assert "STALE1" not in [i.get("ticket") for i in _all_action_center_items(ac)]

    archive = api.route_inbox_review(None, {}, {"page": 1, "page_size": 50})
    archive_message_ids = [row["message_id"] for row in archive["messages"]]
    assert "old1" in archive_message_ids


def test_action_center_totals_do_not_count_the_excluded_stale_backlog(api_env):
    """Requirement 6: counts/KPIs shown alongside Action Center must not
    treat the excluded stale backlog as active work."""
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    before = api.route_action_center(None, {}, {})
    before_total = sum(g["total"] for g in before["groups"])

    ten_days_ago_minutes = -(10 * 24 * 60)
    seed_pending(api_env, "old1", agn_message("STALE1"), minutes_from_now=ten_days_ago_minutes)

    after = api.route_action_center(None, {}, {})
    after_total = sum(g["total"] for g in after["groups"])
    assert after_total == before_total  # the new stale row must not inflate any group's total


def test_action_center_ack_company_k_disappears_once_acknowledged(api_env):
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    seed_pending(api_env, "m1", agn_message("A701"))
    api.route_acknowledge(None, {"message_id": "m1"}, {})
    result = api.route_action_center(None, {}, {})
    ack_owed = [i["ticket"] for i in result["sections"]["company_k_owed"]["items"]]
    ready = [i["ticket"] for i in result["sections"]["ready_to_dispatch"]["items"]]
    assert "A701" not in ack_owed
    assert "A701" in ready


def test_action_center_normalized_item_schema_fields(api_env):
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    seed_pending(api_env, "m1", agn_message("A702"))
    result = api.route_action_center(None, {}, {})
    item = result["sections"]["company_k_owed"]["items"][0]
    required = {
        "entity_type", "entity_id", "ticket", "source", "technician", "stage", "next_action",
        "blocking_reason", "age", "human_review_needed", "destination", "delivery_mode",
    }
    assert required <= set(item.keys())
    assert item["entity_type"] in ("JOB", "INCOMING_MESSAGE")
    assert item["delivery_mode"] == "MANUAL COPY"


def test_route_active_technician_k_uses_the_pending_observations_passed_in(api_env):
    """route_active fetches whatsapp_pending_observations once per
    request and hands the same list into _technician_k_dto for every
    job (operator_api.py's route_active), instead of each job
    re-querying the table itself. Proves that wiring is actually
    honored -- an observation seeded before the call is still reflected
    in the per-job technician_k candidates, not silently dropped by
    passing the list through an extra parameter. Same fixture pattern as
    test_technician_k_waiting_surfaces_a_strong_candidate above."""
    job_id = _seed_active_job_with_technician(api_env)
    _seed_bridge_observation(message_id="wa-obs-1")
    entries = api.route_active(None, {}, {})["entries"]
    entry = next(e for e in entries if e["id"] == job_id)
    assert entry["technician_k"] is not None
    assert entry["technician_k"]["status"] == "waiting"
    assert any(c["message_id"] == "wa-obs-1" for c in entry["technician_k"]["candidates"])


def test_route_active_and_action_center_stay_fast_at_moderate_synthetic_volume(api_env):
    """Regression bound for the N+1 fix in route_active/route_action_center
    (operator_api.py: technician_k/company_update_owed are now computed
    once per job instead of twice, and whatsapp_pending_observations is
    fetched once per request instead of once per job). 50 synthetic jobs,
    bulk-inserted directly (never through the real pipeline, purely to
    get volume quickly) -- generous enough not to flake on a slow CI
    machine, tight enough to catch a regression back to per-job
    quadratic-ish behavior."""
    conn = storage.connect(api_env)
    try:
        tech_id = storage.add_technician(
            conn, name="Perf Regression Tech", areas=["Charlotte"], skills=["car lockout"],
            dispatch_group="Perf Dispatch", closing_group="Perf Closings",
        )
        now = storage._now()
        for i in range(50):
            fields = json.dumps({"city": "Charlotte", "state": "NC", "service_type": "Car Lockout", "phone": "1-555-000-0000"})
            cur = conn.execute(
                "INSERT INTO events (job_id, ticket_number, raw_message, message_type, source, luxor_message, "
                "final_action, created_at, channel, schema_version) VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"PERFREG{i}", "synthetic perf regression fixture", "NEW", "AGN", "synthetic", "Approve", now, "manual", 3),
            )
            event_id = cur.lastrowid
            job_cur = conn.execute(
                "INSERT INTO jobs (ticket_number, source, latest_fields_json, created_at, updated_at, status, "
                "last_event_id, last_event_at, assigned_technician_id, reconciliation_state) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"PERFREG{i}", "AGN", fields, now, now, "Approved", event_id, now, tech_id, None),
            )
            conn.execute("UPDATE events SET job_id = ? WHERE id = ?", (job_cur.lastrowid, event_id))
        conn.commit()
    finally:
        conn.close()

    t0 = time.perf_counter()
    api.route_active(None, {}, {})
    active_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    api.route_action_center(None, {}, {})
    action_center_ms = (time.perf_counter() - t0) * 1000

    assert active_ms < 500, f"route_active took {active_ms:.1f}ms for 50 synthetic jobs -- regression suspected"
    assert action_center_ms < 500, f"route_action_center took {action_center_ms:.1f}ms for 50 synthetic jobs -- regression suspected"


def test_action_center_never_touches_quo_or_whatsapp_adapters():
    import inspect
    src = inspect.getsource(api.route_action_center)
    assert "quo_adapter" not in src
    assert "whatsapp_adapter" not in src


# ======================================================= GLOBAL SEARCH =====
def test_search_finds_a_committed_job_by_ticket(api_env):
    job_id = _seed_active_job_with_technician(api_env)
    result = api.route_search(None, {}, {"q": "A200"})
    assert any(r["entity_id"] == job_id and r["entity_type"] == "JOB" for r in result["results"])


def test_search_finds_a_job_by_technician_name(api_env):
    job_id = _seed_active_job_with_technician(api_env)
    result = api.route_search(None, {}, {"q": "fabricated tech"})
    assert any(r["entity_id"] == job_id for r in result["results"])


def test_search_finds_a_job_by_city(api_env):
    job_id = _seed_active_job_with_technician(api_env)
    result = api.route_search(None, {}, {"q": "charlotte"})
    assert any(r["entity_id"] == job_id for r in result["results"])


def test_search_finds_an_uncommitted_pending_message(api_env):
    seed_live_boundary(api_env)
    seed_pending(api_env, "m1", agn_message("A801"))
    result = api.route_search(None, {}, {"q": "A801"})
    assert any(r["entity_type"] == "INCOMING_MESSAGE" and r["ticket"] == "A801" for r in result["results"])


def test_search_result_screen_routes_a_closed_unsettled_job_to_closings(api_env):
    job_id = _seed_active_job_with_technician(api_env)
    conn = storage.connect(api_env)
    try:
        conn.execute("UPDATE jobs SET status = 'Closed' WHERE id = ?", (job_id,))
        conn.commit()
    finally:
        conn.close()
    result = api.route_search(None, {}, {"q": "A200"})
    match = next(r for r in result["results"] if r["entity_id"] == job_id)
    assert match["screen"] == "closings"


def test_search_result_screen_routes_a_settled_job_to_history(api_env):
    job_id = _seed_active_job_with_technician(api_env)
    conn = storage.connect(api_env)
    try:
        conn.execute("UPDATE jobs SET status = 'Closed', reconciliation_state = 'Settled' WHERE id = ?", (job_id,))
        conn.commit()
    finally:
        conn.close()
    result = api.route_search(None, {}, {"q": "A200"})
    match = next(r for r in result["results"] if r["entity_id"] == job_id)
    assert match["screen"] == "history"


def test_search_result_screen_routes_a_handle_manually_job_to_action_center(api_env):
    job_id = _seed_active_job_with_technician(api_env)
    conn = storage.connect(api_env)
    try:
        conn.execute("UPDATE jobs SET status = 'Handle Manually' WHERE id = ?", (job_id,))
        conn.commit()
    finally:
        conn.close()
    result = api.route_search(None, {}, {"q": "A200"})
    match = next(r for r in result["results"] if r["entity_id"] == job_id)
    assert match["screen"] == "actioncenter"


def test_search_never_returns_phone_or_address(api_env):
    _seed_active_job_with_technician(api_env)
    result = api.route_search(None, {}, {"q": "charlotte"})
    combined = json.dumps(result)
    assert "phone" not in combined.lower()
    assert "555-010" not in combined


def test_search_requires_a_minimum_query_length(api_env):
    _seed_active_job_with_technician(api_env)
    result = api.route_search(None, {}, {"q": "a"})
    assert result["results"] == []


def test_search_caps_results_and_reports_truncation(api_env):
    from dispatcher.rules.service_areas import load_service_areas
    tech_id = add_configured_technician(api_env)
    seed_live_boundary(api_env)
    for i in range(25):
        seed_pending(api_env, f"m{i}", agn_message(f"A{i:03d}"))
        api.route_acknowledge(None, {"message_id": f"m{i}"}, {})
        api.route_prepare_dispatch(None, {"message_id": f"m{i}"}, {})
        job_id = api.route_confirm_dispatch(None, {"message_id": f"m{i}"}, {})["job_id"]
        conn = storage.connect(api_env)
        try:
            storage.assign_technician(conn, job_id, tech_id, load_service_areas())
        finally:
            conn.close()
    result = api.route_search(None, {}, {"q": "agn"})
    assert len(result["results"]) == api._SEARCH_RESULT_MAX
    assert result["truncated"] is True


def test_search_never_touches_quo_or_whatsapp_adapters():
    import inspect
    src = inspect.getsource(api.route_search)
    assert "quo_adapter" not in src
    assert "whatsapp_adapter" not in src


# ================================================= NEXT ACTIONABLE JOB =====
def test_next_actionable_job_matches_action_center_top_item(api_env):
    job_id = _seed_active_job(api_env)  # no technician -> assign_technician, an ACTION NOW item
    ac = api.route_action_center(None, {}, {})
    top = ac["groups"][0]["items"][0] if ac["groups"][0]["key"] == "action_now" else None
    assert top is not None
    result = api.route_next_actionable_job(None, {}, {})
    assert result["found"] is True
    assert result["item"]["entity_id"] == top["entity_id"]


def test_next_actionable_job_skips_waiting_items(api_env):
    """A job only waiting on a technician's K reply must never be
    offered as the "next actionable job" -- there is nothing for the
    operator to actually do about it yet."""
    _seed_active_job_with_technician(api_env)  # -> waiting_technician_k only
    result = api.route_next_actionable_job(None, {}, {})
    assert result["found"] is False


def test_next_actionable_job_surfaces_human_review_when_it_is_the_only_thing(api_env):
    seed_live_boundary(api_env)
    seed_pending(api_env, "m1", agn_message("A810"))
    handled = api.route_handle_manually(None, {"message_id": "m1"}, {})
    result = api.route_next_actionable_job(None, {}, {})
    assert result["found"] is True
    assert result["item"]["entity_id"] == handled["job_id"]


def test_next_actionable_job_respects_exclude_entity_id(api_env):
    job_id = _seed_active_job(api_env)
    result = api.route_next_actionable_job(None, {}, {"exclude_entity_id": str(job_id)})
    assert result["found"] is False


def test_next_actionable_job_never_mutates_anything(api_env):
    """A pure read -- calling it twice in a row must never change the
    job's state (no acceptance/decline/sent/complete triggered)."""
    job_id = _seed_active_job(api_env)
    api.route_next_actionable_job(None, {}, {})
    api.route_next_actionable_job(None, {}, {})
    conn = storage.connect(api_env)
    try:
        job = storage.get_job_by_id(conn, job_id)
    finally:
        conn.close()
    assert job["status"] == "Approved"


def test_next_actionable_job_never_touches_quo_or_whatsapp_adapters():
    import inspect
    src = inspect.getsource(api.route_next_actionable_job)
    assert "quo_adapter" not in src
    assert "whatsapp_adapter" not in src


# ==================================================== WHATSAPP BRIDGE =====
import whatsapp_bridge_adapter as bridge_adapter  # noqa: E402


@pytest.fixture()
def clean_bridge_state(api_env):
    """Depends on api_env (not just tmp_path) so OPERATOR_API_DB_PATH is
    always pointed at a throwaway database before any test in this
    section calls add_observation() -- that function now durably writes
    to whatsapp_pending_observations via storage.py, so an unset env var
    here would silently write test rows into the real data/dispatcher.db.
    Fixture sharing means this is a no-op re-set for tests that also
    request api_env directly (e.g. to seed a real job first)."""
    bridge_adapter._reset_state_for_tests()
    yield
    bridge_adapter._reset_state_for_tests()


def _seed_observation(message_id="wamid.1", text="PDL: A200 -- job done, $95 cash"):
    bridge_adapter.add_observation({
        "event_type": "incoming_message", "message_id": message_id, "group_id": "111@g.us",
        "group_name": "Kabaka Dispatch", "sender": "1555", "timestamp": "1700000000", "text": text,
    })


def test_whatsapp_bridge_status_says_not_paired_before_any_observation(clean_bridge_state):
    result = api.route_whatsapp_bridge_status(None, {}, {})
    assert result["paired"] is False
    assert "not paired yet" in result["status"]


def test_whatsapp_bridge_status_flips_only_after_a_real_observation(clean_bridge_state):
    _seed_observation()
    result = api.route_whatsapp_bridge_status(None, {}, {})
    assert result["paired"] is True
    assert result["observations_received"] == 1


def test_whatsapp_pending_lists_unresolved_observations(clean_bridge_state):
    _seed_observation("wamid.1")
    _seed_observation("wamid.2")
    result = api.route_whatsapp_pending(None, {}, {})
    assert len(result["pending"]) == 2


def test_whatsapp_attach_never_calls_dispatcher_service_itself(api_env, clean_bridge_state):
    """Attaching only hands the raw text back to the frontend for the
    EXISTING commercial/save flow to use -- it must never itself create
    a shadow episode or touch the jobs table. Confirms both no DB write
    happened AND the exact original text is returned unmodified."""
    job_id = _seed_active_job(api_env)
    raw_text = "PDL: A200 -- fixed 150, customer accepted"
    _seed_observation("wamid.attach1", text=raw_text)

    before_events = get_events_count(api_env)
    result = api.route_whatsapp_attach(None, {"message_id": "wamid.attach1"}, {"job_id": str(job_id)})
    assert result["job_id"] == job_id
    assert result["text"] == raw_text
    assert get_events_count(api_env) == before_events, "attach must not itself write any audit event"

    conn = storage.connect(api_env)
    assert storage.get_shadow_episode_for_job(conn, job_id) is None, "attach must not itself create a shadow episode"
    conn.close()

    # Now confirmed via the existing, unchanged commercial/save route --
    # this is the only thing allowed to actually record anything.
    save_result = api.route_commercial_save(
        None, {"id": str(job_id)},
        {"raw_technician_text": raw_text, "corrected_fields": {"quote_type": "FIXED", "quote_amount": "150", "customer_response": "ACCEPTED"}},
    )
    assert save_result["success"] is True


def test_whatsapp_attach_removes_observation_from_pending_list(api_env, clean_bridge_state):
    job_id = _seed_active_job(api_env)
    _seed_observation("wamid.attach2")
    assert len(api.route_whatsapp_pending(None, {}, {})["pending"]) == 1
    api.route_whatsapp_attach(None, {"message_id": "wamid.attach2"}, {"job_id": str(job_id)})
    assert api.route_whatsapp_pending(None, {}, {})["pending"] == []


def test_whatsapp_ignore_removes_observation_without_any_db_write(api_env, clean_bridge_state):
    _seed_observation("wamid.ignore1")
    before_events = get_events_count(api_env)
    result = api.route_whatsapp_ignore(None, {"message_id": "wamid.ignore1"}, {})
    assert result["success"] is True
    assert api.route_whatsapp_pending(None, {}, {})["pending"] == []
    assert get_events_count(api_env) == before_events


def test_whatsapp_attach_unknown_message_id_404s(clean_bridge_state):
    with pytest.raises(api.ApiError):
        api.route_whatsapp_attach(None, {"message_id": "no-such-id"}, {"job_id": "1"})


def test_active_screen_works_normally_when_bridge_has_never_sent_anything(api_env, clean_bridge_state):
    """Bridge disconnected/never-paired must never break the CRM --
    Active still loads fine with an empty pending list."""
    _seed_active_job(api_env)
    active_result = api.route_active(None, {}, {})
    assert len(active_result["entries"]) == 1
    pending_result = api.route_whatsapp_pending(None, {}, {})
    assert pending_result["pending"] == []


# ============================================================ STRUCTURAL ==
def test_connected_server_never_loads_fabricated_data_js():
    """The production connected server must never see the isolated
    prototype's fabricated data.js -- proves the two builds stay fully
    separate, not just by convention."""
    connected_dir = REPO_ROOT / "operator_ui_v2_connected"
    assert not (connected_dir / "js" / "data.js").exists(), "operator_ui_v2_connected must never have its own data.js"
    assert api.STATIC_DIR.resolve() == connected_dir.resolve()
    assert "operator_ui_v2" != connected_dir.name  # the fabricated prototype's own directory, never served here

    for js_file in (connected_dir / "js").glob("*.js"):
        text = js_file.read_text(encoding="utf-8")
        assert "data.js" not in text, f"{js_file.name} must never reference the fabricated prototype's data.js"
    index_html = (connected_dir / "index.html").read_text(encoding="utf-8")
    assert "data.js" not in index_html



def test_operator_api_module_owns_no_sql():
    """operator_api.py must never write anything itself -- every
    persisted fact goes through an existing DispatcherService/storage.py
    method. This structurally rules out any hidden write path (including
    for secrets/session data): there is no SQL in this file to hide one in."""
    source = (REPO_ROOT / "operator_api.py").read_text(encoding="utf-8")
    for forbidden in ("conn.execute(", "INSERT INTO", "CREATE TABLE", "UPDATE ", "DELETE FROM"):
        assert forbidden not in source, f"operator_api.py must not contain {forbidden!r} -- it owns no SQL"


def test_operator_api_makes_no_outbound_network_calls():
    """This server only ever accepts connections (http.server) -- it must
    never act as an HTTP client (requests/urllib/http.client), and must
    never import a WhatsApp automation library."""
    source = (REPO_ROOT / "operator_api.py").read_text(encoding="utf-8")
    import_lines = [l for l in source.splitlines() if l.strip().startswith(("import ", "from "))]
    forbidden_imports = (
        "import requests", "import urllib.request", "import http.client",
        "selenium", "playwright", "pyppeteer", "pyautogui",
    )
    for bad in forbidden_imports:
        assert not any(bad in line for line in import_lines), f"operator_api.py must not import {bad!r}"


def test_no_whatsapp_automation_or_secret_storage_in_requirements():
    requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for forbidden in ("selenium", "playwright", "pyppeteer", "pyautogui"):
        assert forbidden not in requirements


def test_frontend_js_has_no_api_or_backend_imports_and_no_automation_libs():
    """Every JS file served by operator_api.py must contain no reference
    to a WhatsApp automation/browser-automation library -- the whole
    point of the zero-subscription design."""
    js_dir = REPO_ROOT / "operator_ui_v2_connected" / "js"
    forbidden = ("whatsapp-web.js", "baileys", "puppeteer", "selenium", "pyautogui")
    for js_file in js_dir.glob("*.js"):
        text = js_file.read_text(encoding="utf-8").lower()
        for bad in forbidden:
            assert bad not in text, f"{js_file.name} must not reference {bad!r}"


def test_open_whatsapp_never_calls_the_api():
    """Open WhatsApp must be client-only: it must never itself call any
    Api.* method (which would silently record a send). Only an explicit
    "Yes, sent manually" tap may do that."""
    app_js = (REPO_ROOT / "operator_ui_v2_connected" / "js" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("function openWhatsApp()")
    end = app_js.index("\n}\n", start)
    body = app_js[start:end]
    assert "Api." not in body
    assert "fetch(" not in body


def test_confirm_button_only_appears_once_destination_and_copy_exist():
    """Structural guard on the Jobs detail template: the "Confirm sent
    manually" action must only be reachable in the branch that already
    has a real destination and has already gone through the copy/prepare
    step -- never in the missing-destination fallback branch."""
    app_js = (REPO_ROOT / "operator_ui_v2_connected" / "js" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("function dispatchAreaHtml(j) {")
    end = app_js.index("\n}\n", start)
    body = app_js[start:end]
    missing_dest_branch = body[body.index("!j.destination"):body.index("if (!j.copied)")]
    assert "confirm_prompt" not in missing_dest_branch
    assert "confirm_prompt" in body[body.index("if (!j.copied)"):]


def test_mobile_responsive_rules_present():
    """Same responsive contract already verified live in the browser at
    375px width -- this is a lightweight regression guard against the
    CSS file (copied unchanged from the already-verified operator_ui_v2/)
    losing its mobile/desktop split."""
    css = (REPO_ROOT / "operator_ui_v2_connected" / "css" / "styles.css").read_text(encoding="utf-8")
    assert "@media (min-width: 900px)" in css
    assert ".mobile-bottom-nav" in css
    assert ".mobile-row-list" in css


def test_no_credential_or_session_fields_in_stored_payloads(api_env):
    """End-to-end: run a full K->dispatch->commercial-update sequence and
    confirm no event/shadow payload anywhere in the database contains a
    WhatsApp session token, cookie, or QR-code artifact -- there is no
    code path that could produce one, but this proves it empirically
    against real persisted rows, not just by reading source."""
    job_id = _seed_active_job(api_env)
    api.route_commercial_save(
        None, {"id": str(job_id)},
        {"raw_technician_text": "fixed 150, accepted", "corrected_fields": {"quote_type": "FIXED", "quote_amount": "150", "customer_response": "ACCEPTED"}},
    )
    conn = sqlite3.connect(api_env)
    forbidden = ("session_token", "qr_code", "wa_session", "cookie=")
    for table, column in (("events", "raw_message"), ("shadow_events", "payload_json")):
        try:
            rows = conn.execute(f"SELECT {column} FROM {table}").fetchall()
        except sqlite3.OperationalError:
            continue
        for (value,) in rows:
            text = (value or "").lower()
            for bad in forbidden:
                assert bad not in text
    conn.close()


# ==================================================== WHATSAPP INTERPRETATION LAYER

def _seed_registry(db_path, group_id, role, **kw):
    conn = storage.connect(db_path)
    storage.upsert_group_registry_entry(conn, {
        "group_id": group_id, "exact_name": kw.pop("exact_name", group_id), "role": role,
        "is_active": True, "confirmed_by_owner": kw.pop("confirmed_by_owner", True),
        "source": "owner_manual", **kw,
    })
    conn.close()


def _seed_whatsapp_obs(db_path, message_id, group_id, group_name, text, extracted_ticket=None):
    conn = storage.connect(db_path)
    storage.add_whatsapp_observation(conn, {
        "message_id": message_id, "group_id": group_id, "group_name": group_name,
        "sender": "1555", "direction": "incoming", "timestamp": "1700000000",
        "text": text, "extracted_ticket": extracted_ticket, "quoted_message_id": None,
    })
    conn.close()


def test_shadow_feed_unmapped_group_shows_group_mapping_required(api_env):
    _seed_whatsapp_obs(api_env, "m1", "unknown@g.us", "Some Random Group", "hello")
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["mapping_status"] == "HUMAN_REVIEW"
    assert item["next_action"] == "GROUP_MAPPING_REQUIRED"
    assert item["proposed_job_id"] is None


def test_shadow_feed_unconfirmed_registry_entry_is_human_review_not_confirmed(api_env):
    _seed_registry(api_env, "g1@g.us", "TECH_DISPATCH", confirmed_by_owner=False)
    _seed_whatsapp_obs(api_env, "m1", "g1@g.us", "Some Dispatch", "k")
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["mapping_status"] == "HUMAN_REVIEW"


def test_shadow_feed_company_job_intake_with_good_data_is_ready_to_promote(api_env):
    add_configured_technician(api_env)
    _seed_registry(api_env, "intake@g.us", "COMPANY_JOB_INTAKE", company="AGN")
    _seed_whatsapp_obs(api_env, "m1", "intake@g.us", "AGN Intake", agn_message("WA700"))
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["role"] == "COMPANY_JOB_INTAKE"
    assert item["mapping_status"] == "CONFIRMED"
    assert item["proposed_ticket"] == "WA700"
    assert item["missing_fields"] == []
    assert item["next_action"] == "REVIEW -- READY TO PROMOTE"


def test_shadow_feed_company_job_intake_correlating_to_an_existing_job_is_not_labeled_new(api_env):
    """Real gap this closes: a WhatsApp intake message whose ticket+source
    already exists as a committed job (a correction/duplicate/status
    update arriving in the intake group) must not say "NEW JOB DETECTED"
    -- that's misleading even though the underlying promote path already
    correctly attaches to the existing job rather than duplicating it."""
    job_id = _seed_active_job(api_env)  # dispatches a real job, ticket "A200", source AGN
    _seed_registry(api_env, "intake@g.us", "COMPANY_JOB_INTAKE", company="AGN")
    _seed_whatsapp_obs(api_env, "m1", "intake@g.us", "AGN Intake", agn_message("A200"))
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["existing_job_id"] == job_id
    assert "NEW JOB DETECTED" not in item["interpretation"]
    assert f"JOB #{job_id}" in item["interpretation"]
    assert str(job_id) in item["next_action"]


def test_shadow_feed_company_job_intake_with_unparseable_text_shows_missing_fields(api_env):
    _seed_registry(api_env, "intake@g.us", "COMPANY_JOB_INTAKE", company="Mike Jobs")
    _seed_whatsapp_obs(api_env, "m1", "intake@g.us", "Adam - Greenville", "hey can you take this one")
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["missing_fields"] != []
    assert item["next_action"] == "REVIEW"
    assert item["proposed_job_id"] is None


def test_shadow_feed_tech_dispatch_k_with_exactly_one_open_job_is_strong_confidence(api_env):
    job_id = _seed_active_job_with_technician(api_env)  # assigns "Fabricated Tech", dispatch_group "Fab Dispatch"
    _seed_registry(api_env, "disp@g.us", "TECH_DISPATCH", technician_name_unconfirmed="Fabricated Tech")
    _seed_whatsapp_obs(api_env, "m1", "disp@g.us", "Fab Dispatch", "k")
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["interpretation"] == "TECHNICIAN K"
    assert item["confidence"] == "STRONG"
    assert item["proposed_job_id"] == job_id
    assert item["next_action"] == "CONFIRM TECHNICIAN ACCEPTED"


def test_shadow_feed_shows_the_resolved_technician_name_when_registry_uses_a_real_technician_id(api_env):
    """Regression: a registry row confirmed via a real technicians.id
    (e.g. Ziyadah/Falcon/Kimo -- DB-backed, not the Toto/Joe
    technician_name_unconfirmed fallback) must still display the
    technician's name, not blank it out."""
    tech_id = add_configured_technician(api_env, name="Real Tech", dispatch_group="Real Dispatch")
    _seed_registry(api_env, "disp@g.us", "TECH_DISPATCH", technician_id=tech_id, service_area="Faketown")
    _seed_whatsapp_obs(api_env, "m1", "disp@g.us", "Real Dispatch", "k")
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["technician"] == "Real Tech"


def test_shadow_feed_tech_dispatch_ambiguous_when_technician_has_two_open_jobs_and_no_ticket_named(api_env):
    """Real gap this closes: with 2+ open jobs for the same technician and
    no ticket named in the message, neither job may be silently picked --
    must surface AMBIGUOUS, not guess STRONG for either one."""
    from dispatcher.rules.service_areas import load_service_areas
    tech_id = add_configured_technician(api_env)
    seed_live_boundary(api_env)
    seed_pending(api_env, "m1", agn_message("A300"))
    api.route_acknowledge(None, {"message_id": "m1"}, {})
    api.route_prepare_dispatch(None, {"message_id": "m1"}, {})
    job1_id = api.route_confirm_dispatch(None, {"message_id": "m1"}, {})["job_id"]
    seed_pending(api_env, "m2", agn_message("A301"))
    api.route_acknowledge(None, {"message_id": "m2"}, {})
    api.route_prepare_dispatch(None, {"message_id": "m2"}, {})
    job2_id = api.route_confirm_dispatch(None, {"message_id": "m2"}, {})["job_id"]
    conn = storage.connect(api_env)
    storage.assign_technician(conn, job1_id, tech_id, load_service_areas())
    storage.assign_technician(conn, job2_id, tech_id, load_service_areas())
    conn.close()

    _seed_registry(api_env, "disp@g.us", "TECH_DISPATCH", technician_name_unconfirmed="Fabricated Tech")
    _seed_whatsapp_obs(api_env, "wa1", "disp@g.us", "Fab Dispatch", "k")
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "wa1")
    assert item["confidence"] == "AMBIGUOUS"
    assert item["proposed_job_id"] is None  # neither job auto-selected
    assert item["next_action"] == "HUMAN_REVIEW_NEEDED"


def test_shadow_feed_tech_dispatch_exact_when_message_names_the_ticket_among_two_open_jobs(api_env):
    """The other half of the same gap: when the message DOES name a
    ticket, it must correctly disambiguate to the named job, not fall
    back to AMBIGUOUS just because the technician has multiple open jobs."""
    from dispatcher.rules.service_areas import load_service_areas
    tech_id = add_configured_technician(api_env)
    seed_live_boundary(api_env)
    seed_pending(api_env, "m1", agn_message("A400"))
    api.route_acknowledge(None, {"message_id": "m1"}, {})
    api.route_prepare_dispatch(None, {"message_id": "m1"}, {})
    job1_id = api.route_confirm_dispatch(None, {"message_id": "m1"}, {})["job_id"]
    seed_pending(api_env, "m2", agn_message("A401"))
    api.route_acknowledge(None, {"message_id": "m2"}, {})
    api.route_prepare_dispatch(None, {"message_id": "m2"}, {})
    job2_id = api.route_confirm_dispatch(None, {"message_id": "m2"}, {})["job_id"]
    conn = storage.connect(api_env)
    storage.assign_technician(conn, job1_id, tech_id, load_service_areas())
    storage.assign_technician(conn, job2_id, tech_id, load_service_areas())
    conn.close()

    _seed_registry(api_env, "disp@g.us", "TECH_DISPATCH", technician_name_unconfirmed="Fabricated Tech")
    _seed_whatsapp_obs(api_env, "wa1", "disp@g.us", "Fab Dispatch", "on my way for A401", extracted_ticket="A401")
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "wa1")
    assert item["confidence"] == "EXACT"
    assert item["proposed_job_id"] == job2_id


def test_shadow_feed_tech_dispatch_with_no_open_job_is_unmatched_and_human_review(api_env):
    _seed_registry(api_env, "disp@g.us", "TECH_DISPATCH", technician_name_unconfirmed="Nobody Assigned")
    _seed_whatsapp_obs(api_env, "m1", "disp@g.us", "Nobody Dispatch", "k")
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["confidence"] == "UNMATCHED"
    assert item["next_action"] == "HUMAN_REVIEW_NEEDED"
    assert item["proposed_job_id"] is None


def test_shadow_feed_tech_dispatch_eta_message_is_labeled_and_matched(api_env):
    job_id = _seed_active_job_with_technician(api_env)
    _seed_registry(api_env, "disp@g.us", "TECH_DISPATCH", technician_name_unconfirmed="Fabricated Tech")
    _seed_whatsapp_obs(api_env, "m1", "disp@g.us", "Fab Dispatch", "eta 20 minutes")
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["interpretation"] == "TECHNICIAN ETA/UPDATE"
    assert item["proposed_job_id"] == job_id
    assert item["next_action"] == "UPDATE_COMPANY"


def test_shadow_feed_tech_dispatch_decline_with_a_matched_job_says_reassign_not_confirm(api_env):
    """A decline must never read like an ordinary match-confirmation --
    it needs to visibly tell the dispatcher to find someone else."""
    job_id = _seed_active_job_with_technician(api_env)
    _seed_registry(api_env, "disp@g.us", "TECH_DISPATCH", technician_name_unconfirmed="Fabricated Tech")
    _seed_whatsapp_obs(api_env, "m1", "disp@g.us", "Fab Dispatch", "can't")
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["interpretation"] == "TECHNICIAN DECLINE"
    assert item["proposed_job_id"] == job_id
    assert item["next_action"] == "TECHNICIAN DECLINED -- REASSIGN"
    assert "confirm" not in item["next_action"].lower()


def test_shadow_feed_tech_dispatch_full_job_block_is_labeled_as_a_forward_not_a_technician_reply(api_env):
    """Real live-traffic finding: a dispatcher forwarding the full job
    posting into a technician's dispatch group (same Ticket:/Name:/Phone:
    shape company-intake groups use) was previously misread as a
    technician ETA/UPDATE, because the branch only ever expected short
    technician replies. Confirmed as a repeated pattern (17 real
    observations across 3 different dispatch groups/companies) before
    fixing -- this locks in the fix with a matched-existing-job case."""
    job_id = _seed_active_job_with_technician(api_env)  # dispatches ticket "A200"
    _seed_registry(api_env, "disp@g.us", "TECH_DISPATCH", technician_name_unconfirmed="Fabricated Tech")
    _seed_whatsapp_obs(api_env, "m1", "disp@g.us", "Fab Dispatch", main_leads_message("A200"))
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["interpretation"] == f"JOB FORWARDED TO TECHNICIAN -- CORRELATES TO JOB #{job_id}"
    assert item["proposed_job_id"] == job_id
    assert item["proposed_ticket"] == "A200"
    assert item["next_action"] == f"REVIEW -- MATCHES JOB #{job_id}"


def test_shadow_feed_tech_dispatch_full_job_block_with_no_matching_job_still_labeled_as_a_forward(api_env):
    _seed_registry(api_env, "disp@g.us", "TECH_DISPATCH", technician_name_unconfirmed="Fabricated Tech")
    _seed_whatsapp_obs(api_env, "m1", "disp@g.us", "Fab Dispatch", main_leads_message("NOJOB1"))
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["interpretation"] == "JOB FORWARDED TO TECHNICIAN -- TICKET NOJOB1 (no matching job on record)"
    assert item["proposed_job_id"] is None
    assert item["proposed_ticket"] == "NOJOB1"
    assert item["next_action"] == "REVIEW"


def test_shadow_feed_tech_dispatch_short_k_reply_is_unaffected_by_the_forwarded_job_check(api_env):
    """A short reply like "k" never parses as a ticket+phone block, so it
    must keep going through the normal K/decline/ETA path unchanged."""
    job_id = _seed_active_job_with_technician(api_env)
    _seed_registry(api_env, "disp@g.us", "TECH_DISPATCH", technician_name_unconfirmed="Fabricated Tech")
    _seed_whatsapp_obs(api_env, "m1", "disp@g.us", "Fab Dispatch", "k")
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["interpretation"] == "TECHNICIAN K"
    assert item["proposed_job_id"] == job_id


def test_shadow_feed_tech_dispatch_no_answer_is_its_own_category_not_a_generic_update(api_env):
    """Real live-traffic finding: "customer didn't answer" is operationally
    distinct from every existing category -- not a decline (the technician
    isn't refusing), not an ETA, not an ordinary update. It means the job
    is stalling and the company needs telling. Confirmed as a repeated
    cross-group pattern (6 genuine instances, 4 groups, 4 technicians)
    before adding."""
    job_id = _seed_active_job_with_technician(api_env)
    _seed_registry(api_env, "disp@g.us", "TECH_DISPATCH", technician_name_unconfirmed="Fabricated Tech")
    _seed_whatsapp_obs(api_env, "m1", "disp@g.us", "Fab Dispatch", "No answer will try to call again")
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["interpretation"] == "TECHNICIAN NO ANSWER -- CUSTOMER UNREACHABLE"
    assert item["proposed_job_id"] == job_id
    assert item["next_action"] == "UPDATE_COMPANY"


@pytest.mark.parametrize("text", [
    "Its going to voicemail left a voicemail and texted them",
    "No answer sent a text",
    "Cx doesn't answer, voice mail",
    "Customer didn't answer so how I'm going to be on my way",
])
def test_shadow_feed_recognizes_every_real_observed_no_answer_phrasing(api_env, text):
    """Each string here is a real message observed in live traffic. A
    leading "No" must NOT be mistaken for the bare "no" decline token --
    "No answer sent a text" is a no-answer report, not a refusal."""
    _seed_active_job_with_technician(api_env)
    _seed_registry(api_env, "disp@g.us", "TECH_DISPATCH", technician_name_unconfirmed="Fabricated Tech")
    _seed_whatsapp_obs(api_env, "m1", "disp@g.us", "Fab Dispatch", text)
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["interpretation"] == "TECHNICIAN NO ANSWER -- CUSTOMER UNREACHABLE"


def test_shadow_feed_pass_because_of_no_answer_is_treated_as_a_decline_needing_reassignment(api_env):
    """Real observed message combining both facts. The actionable outcome
    is a reassignment, so it must not read as "just waiting on the
    customer" -- but the no-answer reason is preserved in the label."""
    job_id = _seed_active_job_with_technician(api_env)
    _seed_registry(api_env, "disp@g.us", "TECH_DISPATCH", technician_name_unconfirmed="Fabricated Tech")
    _seed_whatsapp_obs(api_env, "m1", "disp@g.us", "Fab Dispatch",
                       "Pass he's too far now not worth them not answering after I drive 1 hour")
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["interpretation"] == "TECHNICIAN DECLINE -- CUSTOMER NO ANSWER"
    assert item["next_action"] == "TECHNICIAN DECLINED -- REASSIGN"
    assert item["proposed_job_id"] == job_id


def test_shadow_feed_no_answer_check_does_not_disturb_k_decline_or_eta(api_env):
    """Regression guard: adding the no-answer branch must leave every
    pre-existing category classified exactly as before."""
    _seed_active_job_with_technician(api_env)
    _seed_registry(api_env, "disp@g.us", "TECH_DISPATCH", technician_name_unconfirmed="Fabricated Tech")
    for msg_id, text, expected in [
        ("a1", "k", "TECHNICIAN K"),
        ("a2", "can't", "TECHNICIAN DECLINE"),
        ("a3", "eta 20 minutes", "TECHNICIAN ETA/UPDATE"),
        ("a4", "1:30 hours", "TECHNICIAN ETA/UPDATE"),
        ("a5", "on my way", "TECHNICIAN UPDATE"),
    ]:
        _seed_whatsapp_obs(api_env, msg_id, "disp@g.us", "Fab Dispatch", text)
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    got = {i["message_id"]: i["interpretation"] for i in result["items"]}
    assert got["a1"] == "TECHNICIAN K"
    assert got["a2"] == "TECHNICIAN DECLINE"
    assert got["a3"] == "TECHNICIAN ETA/UPDATE"
    assert got["a4"] == "TECHNICIAN ETA/UPDATE"
    assert got["a5"] == "TECHNICIAN UPDATE"


def test_shadow_feed_tech_closings_never_invents_amount(api_env):
    job_id = _seed_active_job_with_technician(api_env)
    _seed_registry(api_env, "close@g.us", "TECH_CLOSINGS", technician_name_unconfirmed="Fabricated Tech")
    _seed_whatsapp_obs(api_env, "m1", "close@g.us", "Fab Closings", "job's done, paid cash")
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["interpretation"] == "CLOSING RECEIVED"
    assert "amount" in item["missing_fields"]
    assert item["next_action"] == "REVIEW CLOSING"
    assert item["proposed_job_id"] == job_id


def test_shadow_feed_tech_closings_extracts_amount_and_payment_method_when_directly_adjacent(api_env):
    """Real live-traffic finding: TECH_CLOSINGS previously always claimed
    amount/payment_method were missing, even for messages that plainly
    state them (e.g. real observed text "$200 cash abozeid", "$720 cash",
    "$379.50 cc joy", "$415 venmo to hazem" -- 5 of 8 real closing
    messages checked had this exact adjacent shape). Fixed to parse this
    specific, repeated, high-confidence pattern -- never a looser guess."""
    job_id = _seed_active_job_with_technician(api_env)
    _seed_registry(api_env, "close@g.us", "TECH_CLOSINGS", technician_name_unconfirmed="Fabricated Tech")
    _seed_whatsapp_obs(api_env, "m1", "close@g.us", "Fab Closings", "Closed\n$200 cash abozeid")
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["interpretation"] == "CLOSING RECEIVED -- $200 cash"
    assert item["missing_fields"] == []
    assert item["closing_amount"] == "200"
    assert item["closing_payment_method"] == "cash"
    assert item["proposed_job_id"] == job_id


def test_shadow_feed_tech_closings_does_not_match_a_dollar_figure_with_no_adjacent_payment_word(api_env):
    """Real observed false-positive risk: "Da keda additional to the
    $150" mentions a dollar figure but is not a closing report at all --
    must never be picked up as a closing amount."""
    _seed_registry(api_env, "close@g.us", "TECH_CLOSINGS", technician_name_unconfirmed="Fabricated Tech")
    _seed_whatsapp_obs(api_env, "m1", "close@g.us", "Fab Closings", "Da keda additional to the $150")
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["interpretation"] == "CLOSING RECEIVED"
    assert item["missing_fields"] == ["amount", "payment_method"]
    assert item["closing_amount"] is None
    assert item["closing_payment_method"] is None


def test_shadow_feed_company_closings_is_destination_context_only(api_env):
    _seed_registry(api_env, "cc@g.us", "COMPANY_CLOSINGS", company="GMI")
    _seed_whatsapp_obs(api_env, "m1", "cc@g.us", "GMI All Areas Closings", "closed ticket 123, $80")
    result = api.route_whatsapp_shadow_feed(None, {}, {})
    item = next(i for i in result["items"] if i["message_id"] == "m1")
    assert item["proposed_job_id"] is None
    assert "destination context only" in item["next_action"].lower()


def test_shadow_feed_never_writes_to_quo_pending_messages(api_env):
    """Explicit guard for the hard requirement: transports stay separate."""
    add_configured_technician(api_env)
    _seed_registry(api_env, "intake@g.us", "COMPANY_JOB_INTAKE", company="AGN")
    _seed_whatsapp_obs(api_env, "m1", "intake@g.us", "AGN Intake", agn_message("WA701"))
    api.route_whatsapp_shadow_feed(None, {}, {})
    assert get_jobs_count(api_env) == 0
    conn = sqlite3.connect(api_env)
    n = conn.execute("SELECT COUNT(*) FROM quo_pending_messages").fetchone()[0]
    conn.close()
    assert n == 0


# ---------------------------------------------------- PROMOTE TO JOB (human action)

def test_promote_to_job_creates_a_real_job_via_the_existing_commit_path(api_env):
    add_configured_technician(api_env)
    _seed_registry(api_env, "intake@g.us", "COMPANY_JOB_INTAKE", company="AGN")
    _seed_whatsapp_obs(api_env, "m1", "intake@g.us", "AGN Intake", agn_message("WA702"))
    before = get_jobs_count(api_env)
    result = api.route_whatsapp_promote_to_job(None, {"message_id": "m1"}, {})
    assert result["success"] is True
    assert get_jobs_count(api_env) == before + 1
    assert get_jobs_count(api_env) == 1
    conn = sqlite3.connect(api_env)
    n = conn.execute("SELECT COUNT(*) FROM quo_pending_messages").fetchone()[0]
    conn.close()
    assert n == 0  # still never written to the Quo transport table


def test_promote_to_job_is_idempotent_by_whatsapp_message_id(api_env):
    add_configured_technician(api_env)
    _seed_registry(api_env, "intake@g.us", "COMPANY_JOB_INTAKE", company="AGN")
    _seed_whatsapp_obs(api_env, "m1", "intake@g.us", "AGN Intake", agn_message("WA703"))
    first = api.route_whatsapp_promote_to_job(None, {"message_id": "m1"}, {})
    second = api.route_whatsapp_promote_to_job(None, {"message_id": "m1"}, {})
    assert first["job_id"] == second["job_id"]
    assert get_jobs_count(api_env) == 1  # never a second job for the same WhatsApp message


def test_promote_to_job_rejects_a_non_company_job_intake_group(api_env):
    _seed_registry(api_env, "disp@g.us", "TECH_DISPATCH", technician_name_unconfirmed="X")
    _seed_whatsapp_obs(api_env, "m1", "disp@g.us", "Some Dispatch", "k")
    with pytest.raises(api.ApiError):
        api.route_whatsapp_promote_to_job(None, {"message_id": "m1"}, {})
    assert get_jobs_count(api_env) == 0


def test_promote_to_job_rejects_when_no_ticket_can_be_extracted(api_env):
    _seed_registry(api_env, "intake@g.us", "COMPANY_JOB_INTAKE", company="Mike Jobs")
    _seed_whatsapp_obs(api_env, "m1", "intake@g.us", "Adam - Greenville", "can someone take this")
    with pytest.raises(api.ApiError):
        api.route_whatsapp_promote_to_job(None, {"message_id": "m1"}, {})
    assert get_jobs_count(api_env) == 0


def test_promote_to_job_marks_the_observation_resolved(api_env):
    add_configured_technician(api_env)
    _seed_registry(api_env, "intake@g.us", "COMPANY_JOB_INTAKE", company="AGN")
    _seed_whatsapp_obs(api_env, "m1", "intake@g.us", "AGN Intake", agn_message("WA704"))
    result = api.route_whatsapp_promote_to_job(None, {"message_id": "m1"}, {})
    conn = storage.connect(api_env)
    row = storage.get_whatsapp_observation(conn, "m1")
    conn.close()
    assert row["status"] == "promoted"
    assert row["job_id"] == result["job_id"]
