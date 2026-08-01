"""Focused tests for Command Center: the new kpi_read_model functions
(new_jobs_recent, active_with_technician_count, en_route_count,
verified_closed_amount, job_pipeline_counts, get_command_center) plus
operator_api.route_command_center and the structural/PII/no-fabrication
guards for the frontend. Every test seeds a temporary on-disk SQLite
database via dispatcher.storage.connect(tmp_path) -- never the real
database. Synthetic data only, used solely inside these isolated tests."""

from __future__ import annotations

import json
import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from dispatcher import kpi_read_model as kpi
from dispatcher import storage

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture()
def db(tmp_path):
    db_path = tmp_path / "cc_test.db"
    conn = storage.connect(db_path)
    conn.close()
    return db_path


def _conn(db_path):
    return storage.connect(db_path)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _insert_job(conn, ticket, source, status="New", created_at=None, updated_at=None,
                 last_event_at=None, technician_id=None, city="Charlotte"):
    now = created_at or _iso(datetime.now(timezone.utc))
    fields = json.dumps({"city": city, "state": "NC", "service_type": "Car Lockout", "phone": "1-555-010-3000", "address": "100 Fake St"})
    conn.execute(
        "INSERT INTO jobs (ticket_number, source, latest_fields_json, created_at, updated_at, status, "
        "last_event_at, assigned_technician_id) VALUES (?,?,?,?,?,?,?,?)",
        (ticket, source, fields, now, updated_at or now, status, last_event_at, technician_id),
    )
    conn.commit()
    return conn.execute("SELECT id FROM jobs WHERE ticket_number=? AND source=?", (ticket, source)).fetchone()[0]


def _insert_event(conn, job_id, message_type, final_action=None, created_at=None, closing_amount=None):
    conn.execute(
        "INSERT INTO events (job_id, ticket_number, raw_message, message_type, source, final_action, "
        "created_at, closing_amount) VALUES (?,?,?,?,?,?,?,?)",
        (job_id, "T1", "raw", message_type, "AGN", final_action, created_at or _iso(datetime.now(timezone.utc)), closing_amount),
    )
    conn.commit()


def _insert_technician(conn, name="Demo Tech"):
    return storage.add_technician(conn, name=name, areas=["Charlotte"], skills=["car lockout"])


# ==================================================== NEW JOBS RECENT =====
def test_new_jobs_recent_counts_only_new_status_within_window(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    _insert_job(conn, "R1", "AGN", status="New", created_at=_iso(now - timedelta(minutes=10)))
    _insert_job(conn, "R2", "AGN", status="New", created_at=_iso(now - timedelta(minutes=90)))  # outside 60-min window
    _insert_job(conn, "R3", "AGN", status="Approved", created_at=_iso(now - timedelta(minutes=5)))  # not New anymore
    result = kpi.new_jobs_recent(conn, now, window_minutes=60)
    assert result["value"] == 1
    conn.close()


def test_new_jobs_recent_empty_is_a_real_zero_not_insufficient(db):
    conn = _conn(db)
    result = kpi.new_jobs_recent(conn, datetime.now(timezone.utc))
    assert result["value"] == 0
    assert result["data_quality"] == kpi.DQ_ZERO
    conn.close()


# ============================================= ACTIVE WITH TECHNICIAN =====
def test_active_with_technician_requires_both_status_and_real_assignment(db):
    conn = _conn(db)
    tech = _insert_technician(conn)
    _insert_job(conn, "A1", "AGN", status="Approved", technician_id=tech)  # counts
    _insert_job(conn, "A2", "AGN", status="Approved", technician_id=None)  # dispatched but no tech -- must NOT count
    _insert_job(conn, "A3", "AGN", status="New", technician_id=tech)  # has tech but not dispatched -- must NOT count
    _insert_job(conn, "A4", "AGN", status="In Progress", technician_id=tech)  # counts
    result = kpi.active_with_technician_count(conn)
    assert result["value"] == 2
    conn.close()


def test_active_with_technician_differs_from_performance_screens_active_jobs_count(db):
    """active_jobs_count() (Performance) intentionally includes New jobs
    and jobs with no technician; active_with_technician_count() (Command
    Center) intentionally does not -- these are two different, both
    correct, questions and must not silently converge."""
    conn = _conn(db)
    _insert_job(conn, "B1", "AGN", status="New", technician_id=None)
    assert kpi.active_jobs_count(conn)["value"] == 1
    assert kpi.active_with_technician_count(conn)["value"] == 0
    conn.close()


# ============================================================= EN ROUTE ===
def test_en_route_is_honestly_not_trackable_not_a_fabricated_zero(db):
    conn = _conn(db)
    result = kpi.en_route_count(conn)
    assert result["value"] is None
    assert result["data_quality"] == kpi.DQ_NEEDS_DEFINITION
    conn.close()


# =================================================== VERIFIED CLOSED AMOUNT
def test_verified_closed_amount_sums_only_reliable_numeric_values(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    j1 = _insert_job(conn, "C1", "AGN", status="Closed", updated_at=_iso(now))
    _insert_event(conn, j1, "CLOSING", closing_amount="95")
    j2 = _insert_job(conn, "C2", "AGN", status="Closed", updated_at=_iso(now))
    _insert_event(conn, j2, "CLOSING", closing_amount="not-a-number")
    result = kpi.verified_closed_amount(conn, now - timedelta(hours=1), now + timedelta(hours=1))
    assert result["value"] == 95.0
    assert result["numerator"] == 1  # only the reliable one
    assert result["denominator"] == 2  # both closed jobs counted in the denominator
    conn.close()


def test_verified_closed_amount_incomplete_when_any_closing_unreliable(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    j1 = _insert_job(conn, "C3", "AGN", status="Closed", updated_at=_iso(now))
    _insert_event(conn, j1, "CLOSING", closing_amount="not-a-number")
    result = kpi.verified_closed_amount(conn, now - timedelta(hours=1), now + timedelta(hours=1))
    assert result["data_quality"] == kpi.DQ_INSUFFICIENT
    assert "incomplete" in result["explanation"].lower()
    conn.close()


def test_verified_closed_amount_zero_when_nothing_closed(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    result = kpi.verified_closed_amount(conn, now - timedelta(hours=1), now + timedelta(hours=1))
    assert result["value"] == 0.0
    assert result["data_quality"] == kpi.DQ_ZERO
    conn.close()


def test_verified_closed_amount_strips_dollar_signs_and_commas(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    j1 = _insert_job(conn, "C4", "AGN", status="Closed", updated_at=_iso(now))
    _insert_event(conn, j1, "CLOSING", closing_amount="$1,095.50")
    result = kpi.verified_closed_amount(conn, now - timedelta(hours=1), now + timedelta(hours=1))
    assert result["value"] == 1095.50
    conn.close()


# =============================================================== PIPELINE =
def test_job_pipeline_counts_are_mutually_exclusive(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    targets = kpi.load_targets()
    _insert_job(conn, "P1", "AGN", status="New", created_at=_iso(now - timedelta(minutes=5)))  # New
    _insert_job(conn, "P2", "AGN", status="New", created_at=_iso(now - timedelta(minutes=90)))  # Waiting (>30min target)
    _insert_job(conn, "P3", "AGN", status="Approved")  # Assigned
    _insert_job(conn, "P4", "AGN", status="In Progress")  # Closing Due
    _insert_job(conn, "P5", "AGN", status="Closed", updated_at=_iso(now))  # Completed
    _insert_job(conn, "P6", "AGN", status="Cancelled", created_at=_iso(now))  # Cancelled
    counts = kpi.job_pipeline_counts(conn, now - timedelta(hours=1), now + timedelta(hours=1), now, targets)
    assert counts == {"New": 1, "Waiting": 1, "Assigned": 1, "Closing Due": 1, "Completed": 1, "Cancelled": 1}
    conn.close()


# ======================================================== ATTENTION AGE ===
def test_attention_now_with_age_enriches_without_changing_attention_now_itself(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    targets = kpi.load_targets()
    stale_at = now - timedelta(minutes=200)
    _insert_job(conn, "S1", "AGN", status="Approved", last_event_at=_iso(stale_at), updated_at=_iso(stale_at))
    enriched = kpi._attention_now_with_age(conn, now, targets)
    plain = kpi.attention_now(conn, now, targets)
    assert len(enriched) == len(plain) == 1
    assert enriched[0]["age_minutes"] >= 199
    assert "age_minutes" not in plain[0]  # attention_now()'s own contract is untouched
    conn.close()


def test_attention_now_with_age_caps_at_five(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    targets = kpi.load_targets()
    old = now - timedelta(minutes=60)
    for i in range(8):
        _insert_job(conn, f"S{i}", "AGN", status="New", created_at=_iso(old))
    enriched = kpi._attention_now_with_age(conn, now, targets)
    assert len(enriched) == 5
    conn.close()


# ==================================================== GET_COMMAND_CENTER ==
def test_get_command_center_never_exposes_customer_pii(db):
    """No phone/address/customer-name field anywhere in the composed
    dashboard -- it must only ever contain aggregate counts/labels."""
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    tech = _insert_technician(conn)
    _insert_job(conn, "PII1", "AGN", status="Approved", technician_id=tech)
    dashboard = kpi.get_command_center(conn, now=now)
    conn.close()
    blob = json.dumps(dashboard)
    for forbidden in ("phone", "address", "customer", "555-010", "100 Fake St"):
        assert forbidden not in blob.lower() if forbidden.islower() else forbidden not in blob


def test_get_command_center_deterministic_across_two_calls(db):
    """Same inputs -> same outputs. No randomness, no fabricated jitter."""
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    tech = _insert_technician(conn)
    _insert_job(conn, "D1", "AGN", status="Approved", technician_id=tech)
    first = kpi.get_command_center(conn, now=now)
    second = kpi.get_command_center(conn, now=now)
    conn.close()
    assert first["kpis"] == second["kpis"]
    assert first["charts"]["job_pipeline_today"] == second["charts"]["job_pipeline_today"]


def test_get_command_center_composes_only_existing_functions_not_duplicate_sql():
    """Structural: get_command_center()'s own body must not contain a
    second, hand-rolled SQL query for anything already covered by an
    existing kpi_read_model function (jobs_waiting_over_target,
    closings_due_count, completed_jobs, new_jobs_by_source,
    technicians_summary) -- it must only call them."""
    source = (REPO_ROOT / "src" / "dispatcher" / "kpi_read_model.py").read_text(encoding="utf-8")
    start = source.index("def get_command_center(")
    end = source.index("\ndef get_dashboard(")
    body = source[start:end]
    for reused_call in (
        "jobs_waiting_over_target(", "closings_due_count(", "completed_jobs(",
        "new_jobs_by_source(", "technicians_summary(", "_attention_now_with_age(",
    ):
        assert reused_call in body, f"get_command_center must reuse {reused_call} rather than recompute it"
    # _attention_now_with_age() itself must call the real attention_now(),
    # not recompute the SLA-exception logic a second time.
    helper_start = source.index("def _attention_now_with_age(")
    helper_end = source.index("\ndef get_command_center(")
    assert "attention_now(conn, now, targets)" in source[helper_start:helper_end]


# ============================================================ ROUTE TESTS =
def test_route_command_center_returns_real_shape(monkeypatch, db):
    import operator_api as api

    monkeypatch.setenv("OPERATOR_API_DB_PATH", str(db))
    result = api.route_command_center(None, {}, {})
    assert set(result.keys()) >= {"kpis", "verified_closed_amount_today", "attention_now", "charts"}
    assert set(result["kpis"].keys()) == {
        "new_last_60_minutes", "waiting_for_assignment", "active_with_technician",
        "en_route", "closings_due", "completed_today",
    }


def test_route_command_center_merges_whatsapp_pending_into_attention(monkeypatch, db):
    import operator_api as api
    import whatsapp_bridge_adapter

    monkeypatch.setenv("OPERATOR_API_DB_PATH", str(db))
    whatsapp_bridge_adapter._reset_state_for_tests()
    whatsapp_bridge_adapter.add_observation({
        "event_type": "incoming_message", "message_id": "wa-cc-1", "group_id": "1@g.us",
        "group_name": "Demo Dispatch", "sender": "tech", "timestamp": "1700000000", "text": "done",
    })
    result = api.route_command_center(None, {}, {})
    types = [item["type"] for item in result["attention_now"]]
    assert "whatsapp_observation_pending" in types
    whatsapp_bridge_adapter._reset_state_for_tests()


def test_route_new_jobs_reports_real_total_pending_count(monkeypatch, db):
    """Regression guard for the '46/47 older unresolved' audit finding:
    the response must always include the real, uncapped backlog size,
    never just the capped page it actually fetched."""
    import operator_api as api

    monkeypatch.setenv("OPERATOR_API_DB_PATH", str(db))
    result = api.route_new_jobs(None, {}, {})
    assert "total_pending_messages" in result
    assert result["total_pending_messages"] == 0


# ======================================================= FRONTEND GUARDS =
def test_jobs_screen_no_longer_labels_raw_messages_as_unresolved_jobs():
    """Regression guard for the audit: the Jobs screen's HTML must never
    say 'unresolved jobs' again -- raw Quo/WhatsApp messages pending
    triage are not jobs."""
    html = (REPO_ROOT / "operator_ui_v2_connected" / "index.html").read_text(encoding="utf-8")
    assert "unresolved jobs" not in html.lower()
    assert "Archive / Needs Review" in html


def test_frontend_never_hardcodes_fabricated_dashboard_numbers():
    """Structural: app.js's Command Center rendering functions must only
    ever read fields off the API response object -- no literal numeric
    KPI value may be hardcoded as a fallback/placeholder."""
    js = (REPO_ROOT / "operator_ui_v2_connected" / "js" / "app.js").read_text(encoding="utf-8")
    start = js.index("COMMAND CENTER ===")
    end = js.index("=============================================================== ACTIVE */")
    cc_block = js[start:end]
    # No suspicious hardcoded placeholder values that look like demo data
    # leaking into production rendering logic.
    assert not re.search(r"=\s*(1234|9999|\"?\$?100\.00\"?)\s*[;,)]", cc_block)


def test_command_center_kpi_cards_link_to_a_real_screen():
    """Every KPI card must carry a data-screen value that is one of the
    app's real screens -- 'deep link' must point somewhere real, not a
    dead/placeholder target."""
    js = (REPO_ROOT / "operator_ui_v2_connected" / "js" / "app.js").read_text(encoding="utf-8")
    real_screens = {"jobs", "active", "closings", "history", "commandcenter", "performance", "technicians", "learning"}
    for match in re.finditer(r'ccKpiCardHtml\("[^"]+",\s*[\w.]+,\s*"(\w+)"\)', js):
        assert match.group(1) in real_screens


def test_command_center_screen_registered_in_html_and_nav():
    html = (REPO_ROOT / "operator_ui_v2_connected" / "index.html").read_text(encoding="utf-8")
    assert 'id="screen-commandcenter"' in html
    assert 'data-screen="commandcenter"' in html


def test_mobile_and_desktop_nav_match_the_required_structure():
    html = (REPO_ROOT / "operator_ui_v2_connected" / "index.html").read_text(encoding="utf-8")
    desktop_nav = html[html.index('id="desktopSidebar"'):html.index("</nav>", html.index('id="desktopSidebar"'))]
    for expected in ("commandcenter", "jobs", "active", "closings", "history"):
        assert f'data-screen="{expected}"' in desktop_nav

    mobile_nav = html[html.index('id="mobileBottomNav"'):html.index("</nav>", html.index('id="mobileBottomNav"'))]
    for expected in ("commandcenter", "jobs", "active", "closings"):
        assert f'data-screen="{expected}"' in mobile_nav
    assert "More" in mobile_nav
    assert 'data-screen="history"' not in mobile_nav  # Completed moved into "More" on mobile


def test_login_screen_has_command_center_branding():
    html = (REPO_ROOT / "operator_ui_v2_connected" / "index.html").read_text(encoding="utf-8")
    login_block = html[html.index('id="loginScreen"'):html.index('id="appShell"')]
    assert "Dispatch Command Center" in login_block
    assert "LUXOR" in login_block
