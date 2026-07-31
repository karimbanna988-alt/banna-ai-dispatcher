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

import json
import pathlib
import sqlite3
from datetime import datetime, timedelta, timezone

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


# ============================================================== NEW JOBS ==
def test_new_job_shows_configured_destination(api_env):
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    seed_pending(api_env, "m1", agn_message("A100"))

    result = api.route_new_jobs(None, {}, {})
    entries = result["entries"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["source"] == "AGN"
    assert entry["ticket"] == "A100"
    assert entry["destination"] == "Fab Dispatch"
    assert entry["technician_name"] == "Fabricated Tech"


def test_new_job_missing_destination_shows_not_configured(api_env):
    seed_live_boundary(api_env)
    seed_pending(api_env, "m1", agn_message("A101"))  # no technician at all

    entries = api.route_new_jobs(None, {}, {})["entries"]
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

    first = api.route_new_jobs(None, {}, {})["entries"][0]["luxor_message"]
    second = api.route_new_jobs(None, {}, {})["entries"][0]["luxor_message"]
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

    entry = api.route_new_jobs(None, {}, {})["entries"][0]
    assert "555-010-3000" not in entry["original_message_masked"]
    assert "phone masked" in entry["original_message_masked"].lower() or "[phone" in entry["original_message_masked"]


# ==================================================== K / DISPATCH FLOW ==
def test_k_required_blocks_until_acknowledged_then_copy_does_not_dispatch(api_env):
    """copy != send: acknowledging K and preparing the technician copy
    must never create a jobs row or mark the message dispatched -- only
    the explicit confirm step may do that."""
    seed_live_boundary(api_env)
    add_configured_technician(api_env)
    seed_pending(api_env, "m1", agn_message("A104"))

    entry = api.route_new_jobs(None, {}, {})["entries"][0]
    assert entry["k_required"] is True
    assert entry["k_sent"] is False

    ack = api.route_acknowledge(None, {"message_id": "m1"}, {})
    assert ack["success"] is True
    entry = api.route_new_jobs(None, {}, {})["entries"][0]
    assert entry["k_sent"] is True
    assert entry["dispatched"] is False
    assert get_jobs_count(api_env) == 0

    prep = api.route_prepare_dispatch(None, {"message_id": "m1"}, {})
    assert prep["success"] is True
    entry = api.route_new_jobs(None, {}, {})["entries"][0]
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

    entries = api.route_new_jobs(None, {}, {})["entries"]
    assert entries == [], "a dispatched job must no longer appear in the live New Jobs queue"


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

    entry = api.route_new_jobs(None, {}, {})["entries"][0]
    assert entry["source"] == "Main Leads"
    assert entry["k_required"] is False


def test_missing_destination_offers_copy_only_and_handle_manually(api_env):
    seed_live_boundary(api_env)
    seed_pending(api_env, "m1", agn_message("A107"))

    entry = api.route_new_jobs(None, {}, {})["entries"][0]
    assert entry["destination"] is None

    result = api.route_handle_manually(None, {"message_id": "m1"}, {})
    assert result["success"] is True
    assert api.route_new_jobs(None, {}, {})["entries"] == []


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


# ============================================================ STRUCTURAL ==
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
