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
                 last_event_at=None, technician_id=None, city="Charlotte", reconciliation_state=None):
    now = created_at or _iso(datetime.now(timezone.utc))
    fields = json.dumps({"city": city, "state": "NC", "service_type": "Car Lockout", "phone": "1-555-010-3000", "address": "100 Fake St"})
    conn.execute(
        "INSERT INTO jobs (ticket_number, source, latest_fields_json, created_at, updated_at, status, "
        "last_event_at, assigned_technician_id, reconciliation_state) VALUES (?,?,?,?,?,?,?,?,?)",
        (ticket, source, fields, now, updated_at or now, status, last_event_at, technician_id, reconciliation_state),
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
    _insert_job(conn, "P3", "AGN", status="Approved")  # Approved / Assigned
    _insert_job(conn, "P4", "AGN", status="In Progress")  # In Progress
    _insert_job(conn, "P5", "AGN", status="Closed", updated_at=_iso(now))  # Closed
    _insert_job(conn, "P6", "AGN", status="Cancelled", created_at=_iso(now))  # Cancelled
    # A Closed-but-unsettled job: counts toward closings_due_count() (broader
    # definition) but must land in "Closed" here, not "In Progress" -- proves
    # the pipeline's In Progress bucket uses in_progress_count(), never
    # closings_due_count(), so this job is never double-counted.
    _insert_job(conn, "P7", "AGN", status="Closed", updated_at=_iso(now), reconciliation_state=None)
    counts = kpi.job_pipeline_counts(conn, now - timedelta(hours=1), now + timedelta(hours=1), now, targets)
    assert counts == {
        "New": 1, "Waiting": 1, "Approved / Assigned": 1,
        "In Progress": 1, "Closed": 2, "Cancelled": 1,
    }
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


def test_attention_now_with_age_sorts_most_urgent_first(db):
    """Sort by urgency (Phase 6C Part 4): the oldest (most overdue) real
    exception must come first, and the 5 kept after capping must be the
    5 oldest -- not just the first 5 attention_now() happened to return."""
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    targets = kpi.load_targets()
    # Insert 6 stale-active jobs with distinct, increasing staleness so
    # only the 5 oldest should survive the cap, in oldest-first order.
    for i in range(6):
        stale_at = now - timedelta(minutes=200 + i * 10)
        _insert_job(conn, f"U{i}", "AGN", status="Approved", last_event_at=_iso(stale_at), updated_at=_iso(stale_at))
    result = kpi._attention_now_with_age(conn, now, targets)
    ages = [item["age_minutes"] for item in result]
    assert ages == sorted(ages, reverse=True)
    assert len(result) == 5
    # The single newest (least urgent, i=0) must have been dropped by the cap.
    assert not any(item["ticket"] == "U0" for item in result)
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
    assert first["charts"]["job_pipeline"] == second["charts"]["job_pipeline"]


def test_get_command_center_rejects_unknown_chart_period(db):
    conn = _conn(db)
    with pytest.raises(ValueError):
        kpi.get_command_center(conn, chart_period="not-a-real-period")
    conn.close()


def test_get_command_center_chart_period_affects_only_charts_not_top_kpis(db):
    """The top KPI cards (Part 3) always stay 'today'/'right now' -- only
    the charts section (Part 6) honors chart_period."""
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    _insert_job(conn, "CP1", "AGN", status="Closed", updated_at=_iso(now - timedelta(days=10)))
    today_view = kpi.get_command_center(conn, now=now, chart_period="today")
    month_view = kpi.get_command_center(conn, now=now, chart_period="30d")
    conn.close()
    assert today_view["kpis"] == month_view["kpis"]
    assert today_view["charts"]["job_pipeline"]["Closed"] == 0
    assert month_view["charts"]["job_pipeline"]["Closed"] == 1


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
        "jobs_waiting_over_target(", "closings_due_count(", "in_progress_count(", "completed_jobs(",
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
        "in_progress", "closings_due", "completed_today",
    }
    assert set(result["charts"].keys()) == {
        "job_source_mix", "job_pipeline", "technician_activity", "verified_closings",
    }


def test_route_command_center_accepts_chart_period_query_param(monkeypatch, db):
    import operator_api as api

    monkeypatch.setenv("OPERATOR_API_DB_PATH", str(db))
    result = api.route_command_center(None, {}, {"chart_period": "30d"})
    assert result["period"]["chart_period"] == "30d"


def test_route_command_center_rejects_invalid_chart_period(monkeypatch, db):
    import operator_api as api

    monkeypatch.setenv("OPERATOR_API_DB_PATH", str(db))
    with pytest.raises(api.ApiError):
        api.route_command_center(None, {}, {"chart_period": "not-a-real-period"})


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
    """Regression guard: the whole frontend must never say "unresolved
    jobs" anywhere -- raw Quo/WhatsApp messages pending triage are not
    jobs. Archive / Inbox Review is the accurate replacement label."""
    html = (REPO_ROOT / "operator_ui_v2_connected" / "index.html").read_text(encoding="utf-8")
    js = (REPO_ROOT / "operator_ui_v2_connected" / "js" / "app.js").read_text(encoding="utf-8")
    assert "unresolved jobs" not in html.lower()
    assert "unresolved jobs" not in js.lower()
    assert "Archive / Inbox Review" in html


def test_jobs_screen_has_no_old_message_fallback():
    """Regression guard: the Jobs screen must never auto-insert an older
    message when the live (<=60 min) bucket is empty -- the exact
    'recent unresolved' / 'needs review' fallback this phase removed."""
    html = (REPO_ROOT / "operator_ui_v2_connected" / "index.html").read_text(encoding="utf-8")
    js = (REPO_ROOT / "operator_ui_v2_connected" / "js" / "app.js").read_text(encoding="utf-8")
    for stale_id in ("recentJobsSection", "recentJobsTableBody", "olderJobsDetails", "olderJobsTableBody"):
        assert stale_id not in html
        assert stale_id not in js
    assert "No new actionable jobs received in the last 60 minutes." in html


def test_command_center_and_active_never_reference_quo_pending_messages():
    """Regression guard (critical correction #7): the Command Center and
    Active screens' own JS rendering code must never read quo_pending_messages
    or any raw-message field shape -- they consume only /api/command_center
    and /api/active, which are backed exclusively by kpi_read_model.py and
    rm.get_active_jobs_detailed() (real jobs/events tables)."""
    js = (REPO_ROOT / "operator_ui_v2_connected" / "js" / "app.js").read_text(encoding="utf-8")
    cc_start = js.index("COMMAND CENTER ===")
    cc_end = js.index("=============================================================== ACTIVE */")
    cc_block = js[cc_start:cc_end]
    assert "quo_pending_messages" not in cc_block
    assert "getNewJobs" not in cc_block
    assert "getInboxReview" not in cc_block


def test_get_command_center_never_queries_quo_pending_messages_table():
    """Structural: kpi_read_model.py must never reference
    quo_pending_messages -- it is documented as SQL-only against
    jobs/events/shadow_episodes."""
    source = (REPO_ROOT / "src" / "dispatcher" / "kpi_read_model.py").read_text(encoding="utf-8")
    assert "quo_pending_messages" not in source


def test_valid_job_statuses_unchanged():
    """Regression guard (critical correction #9): the real database
    status enum must remain exactly these 8 values -- no invented
    status like 'ASSIGNED' or 'EN_ROUTE' may ever be added."""
    assert storage.VALID_JOB_STATUSES == frozenset({
        "New", "Needs Review", "Approved", "Handle Manually",
        "Rejected", "In Progress", "Closed", "Cancelled",
    })


def test_stage_badges_are_derived_labels_not_database_statuses():
    """Regression guard (critical correction #10): 'Waiting for
    technician'/'Assigned'/'Closing needed' must be computed labels
    derived from real status/assignment/events -- they must never be
    written anywhere as if they were real jobs.status values."""
    js = (REPO_ROOT / "operator_ui_v2_connected" / "js" / "app.js").read_text(encoding="utf-8")
    assert 'status === "Waiting for technician"' not in js
    assert 'status === "Assigned"' not in js
    assert 'status === "Closing needed"' not in js
    # The real derivation source (existing, audited backend label).
    assert "ccStageBadge(nextAction)" in js


# ==================================================== ARCHIVE PAGINATION =
def test_archive_is_truly_paginated_not_a_single_capped_fetch(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    for i in range(30):
        conn.execute(
            "INSERT INTO quo_pending_messages (message_id, group_label, raw_text, received_at, fetched_at, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (f"arc-{i}", "AGN", f"message body {i}", _iso(now - timedelta(minutes=i)), _iso(now)),
        )
    conn.commit()

    from luxor_ops_shared import paginated_pending_messages

    page1, total1 = paginated_pending_messages(conn, page=1, page_size=10)
    page2, total2 = paginated_pending_messages(conn, page=2, page_size=10)
    assert total1 == total2 == 30
    assert len(page1) == 10
    assert len(page2) == 10
    page1_ids = {m["message_id"] for m in page1}
    page2_ids = {m["message_id"] for m in page2}
    assert page1_ids.isdisjoint(page2_ids), "pages must not overlap"
    conn.close()


def test_archive_page_size_is_capped_server_side(db):
    from luxor_ops_shared import paginated_pending_messages, MAX_ARCHIVE_PAGE_SIZE

    conn = _conn(db)
    _rows, _total = paginated_pending_messages(conn, page=1, page_size=999999)
    conn.close()
    # The cap is enforced by clamping page_size before the query runs --
    # verify the constant itself is a real, small, sane limit, never
    # "unlimited".
    assert 1 < MAX_ARCHIVE_PAGE_SIZE <= 100


def test_archive_never_dispatched_filter_is_a_real_sql_filter_not_fabricated(db):
    """'Unmatched messages' (the required accurate label) maps to a real,
    cheap SQL condition -- no dispatch_tracking row with
    dispatched_manually_at set -- never a fabricated classification."""
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO quo_pending_messages (message_id, group_label, raw_text, received_at, fetched_at, status) "
        "VALUES ('matched', 'AGN', 'text', ?, ?, 'pending')", (_iso(now), _iso(now)),
    )
    conn.execute(
        "INSERT INTO quo_pending_messages (message_id, group_label, raw_text, received_at, fetched_at, status) "
        "VALUES ('unmatched', 'AGN', 'text', ?, ?, 'pending')", (_iso(now), _iso(now)),
    )
    conn.execute(
        "INSERT INTO dispatch_tracking (message_id, dispatched_manually_at) VALUES ('matched', ?)", (_iso(now),),
    )
    conn.commit()

    from luxor_ops_shared import paginated_pending_messages

    rows, total = paginated_pending_messages(conn, unmatched_only=True)
    conn.close()
    assert total == 1
    assert rows[0]["message_id"] == "unmatched"


def test_route_inbox_review_never_exposes_phone_numbers(monkeypatch, db):
    import operator_api as api

    monkeypatch.setenv("OPERATOR_API_DB_PATH", str(db))
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO quo_pending_messages (message_id, group_label, raw_text, from_number, received_at, fetched_at, status) "
        "VALUES ('pii1', 'AGN', 'Call me at 555-010-3000 please', '+15550103000', ?, ?, 'pending')",
        (_iso(now), _iso(now)),
    )
    conn.commit()
    conn.close()

    result = api.route_inbox_review(None, {}, {})
    blob = json.dumps(result)
    assert "555-010-3000" not in blob
    assert "+15550103000" not in blob
    assert "from_number" not in blob


def test_route_inbox_review_label_never_calls_messages_jobs(monkeypatch, db):
    """The label may explain that these are NOT jobs (so the word 'job'
    legitimately appears in that clarifying context), but must never
    assert or imply that they ARE jobs -- 'active jobs'/'unresolved
    jobs' phrasing is exactly what this regression guards against."""
    import operator_api as api

    monkeypatch.setenv("OPERATOR_API_DB_PATH", str(db))
    result = api.route_inbox_review(None, {}, {})
    label = result["label"].lower()
    assert "active jobs" not in label
    assert "unresolved jobs" not in label
    assert "not confirmed jobs" in label


# ============================================== LIVE JOBS 60-MINUTE WINDOW
def test_route_new_jobs_live_bucket_is_exactly_the_60_minute_window(monkeypatch, tmp_path):
    import operator_api as api
    from dispatcher import storage as _storage

    db_path = tmp_path / "live_window.db"
    conn = _storage.connect(db_path)
    conn.execute(
        "INSERT INTO console_activation_state (id, live_queue_started_at, updated_at) VALUES (1, ?, ?)",
        ("2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("OPERATOR_API_DB_PATH", str(db_path))

    result = api.route_new_jobs(None, {}, {})
    assert result["live_window_minutes"] == 60
    for entry in result["live"]:
        assert entry["age_minutes"] <= 60


# ==================================== NO SEND PATH / REAL DB SAFETY GUARDS
def test_no_quo_or_whatsapp_send_call_introduced_this_phase():
    """Structural: neither the new inbox_review route nor the archive
    frontend code may call a Quo/WhatsApp send-capable method."""
    api_source = (REPO_ROOT / "operator_api.py").read_text(encoding="utf-8")
    inbox_review_start = api_source.index("def route_inbox_review(")
    inbox_review_end = api_source.index("\ndef route_sync(")
    inbox_review_body = api_source[inbox_review_start:inbox_review_end]
    for forbidden in (".send_message(", ".sendMessage(", "quo_client.send", "post_message"):
        assert forbidden not in inbox_review_body

    js = (REPO_ROOT / "operator_ui_v2_connected" / "js" / "app.js").read_text(encoding="utf-8")
    archive_start = js.index("ARCHIVE / INBOX ====")
    archive_end = js.index("function jobRowHtml(")
    archive_block = js[archive_start:archive_end]
    for forbidden in ("sendMessage", "/api/sync_send", "whatsapp://send"):
        assert forbidden not in archive_block


def test_paginated_pending_messages_never_writes_to_the_database(db):
    """paginated_pending_messages() must be read-only -- run it, then
    confirm the row count and every row's status are byte-identical."""
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    for i in range(5):
        conn.execute(
            "INSERT INTO quo_pending_messages (message_id, group_label, raw_text, received_at, fetched_at, status) "
            "VALUES (?, 'AGN', 'text', ?, ?, 'pending')",
            (f"ro-{i}", _iso(now), _iso(now)),
        )
    conn.commit()
    before = conn.execute("SELECT message_id, status FROM quo_pending_messages ORDER BY message_id").fetchall()

    from luxor_ops_shared import paginated_pending_messages
    for page in range(1, 4):
        paginated_pending_messages(conn, page=page, page_size=2, search="text", unmatched_only=True)
        paginated_pending_messages(conn, page=page, page_size=2)

    after = conn.execute("SELECT message_id, status FROM quo_pending_messages ORDER BY message_id").fetchall()
    conn.close()
    assert before == after


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


# =================================================== PHASE 6C: VISUAL/UX ==
def test_login_screen_has_a_password_visibility_toggle():
    html = (REPO_ROOT / "operator_ui_v2_connected" / "index.html").read_text(encoding="utf-8")
    login_block = html[html.index('id="loginScreen"'):html.index('id="appShell"')]
    assert 'id="loginPasswordToggle"' in login_block
    js = (REPO_ROOT / "operator_ui_v2_connected" / "js" / "app.js").read_text(encoding="utf-8")
    assert "loginPasswordToggle" in js
    assert 'input.type = showing ? "password" : "text"' in js


def test_login_distinguishes_network_failure_from_invalid_password():
    """Part 1 requires a distinct 'server unavailable' state, not just
    whatever generic message the server's own 401 body happens to say."""
    js = (REPO_ROOT / "operator_ui_v2_connected" / "js" / "app.js").read_text(encoding="utf-8")
    attempt_start = js.index("async function attemptLogin(")
    attempt_end = js.index("\ndocument.getElementById(\"loginSubmitBtn\")")
    block = js[attempt_start:attempt_end]
    assert "unreachable" in block
    assert "Can't reach the server" in block


def test_command_center_has_a_chart_period_selector():
    html = (REPO_ROOT / "operator_ui_v2_connected" / "index.html").read_text(encoding="utf-8")
    assert 'id="ccChartPeriodSelect"' in html
    for expected_value in ('"today"', '"shift"', '"7d"', '"30d"'):
        assert expected_value.strip('"') in html

    js = (REPO_ROOT / "operator_ui_v2_connected" / "js" / "app.js").read_text(encoding="utf-8")
    assert "ccChartPeriodSelect" in js
    assert "getCommandCenter(ccState.chartPeriod)" in js


def test_mobile_bottom_nav_has_safe_area_clearance_in_main_content():
    """Part 10: 'mobile bottom navigation does not cover content' --
    main-content must reserve space for the fixed bottom nav at mobile
    widths."""
    css = (REPO_ROOT / "operator_ui_v2_connected" / "css" / "styles.css").read_text(encoding="utf-8")
    assert "safe-area-inset-bottom" in css
    assert ".main-content" in css


# ============================ ACCEPTANCE CHECK: IN PROGRESS / CLOSINGS DUE =
def test_in_progress_count_is_status_in_progress_only(db):
    """in_progress_count() must count exactly status='In Progress' jobs
    -- nothing broader, nothing narrower, no EN_ROUTE inference."""
    conn = _conn(db)
    _insert_job(conn, "IP1", "AGN", status="In Progress")
    _insert_job(conn, "IP2", "AGN", status="In Progress")
    _insert_job(conn, "IP3", "AGN", status="Approved")  # not In Progress -- must not count
    _insert_job(conn, "IP4", "AGN", status="Closed", reconciliation_state=None)  # not In Progress -- must not count
    result = kpi.in_progress_count(conn)
    conn.close()
    assert result["value"] == 2
    assert result["data_quality"] == "ok"


def test_closings_due_count_includes_closed_but_unsettled_jobs(db):
    """closings_due_count() must match the real Closings workspace
    definition (luxor_desk_read_model.get_closing_candidates()): In
    Progress jobs, PLUS Closed jobs not yet reconciliation_state ==
    'Settled'. A Settled Closed job must NOT count -- it has left the
    Closings workspace for good."""
    conn = _conn(db)
    _insert_job(conn, "CD1", "AGN", status="In Progress")
    _insert_job(conn, "CD2", "AGN", status="Closed", reconciliation_state=None)  # unsettled -- counts
    _insert_job(conn, "CD3", "AGN", status="Closed", reconciliation_state="Settled")  # settled -- must not count
    _insert_job(conn, "CD4", "AGN", status="Approved")  # not a closing candidate at all
    result = kpi.closings_due_count(conn)
    conn.close()
    assert result["value"] == 2


def test_in_progress_and_closings_due_are_independently_computed_not_duplicates(db):
    """The two KPI cards must be able to diverge -- proving they are not
    the same query wearing two labels. A Closed-but-unsettled job counts
    toward Closings Due but not In Progress."""
    conn = _conn(db)
    _insert_job(conn, "D1", "AGN", status="In Progress")
    _insert_job(conn, "D2", "AGN", status="Closed", reconciliation_state=None)
    dashboard = kpi.get_command_center(conn, now=datetime.now(timezone.utc))
    conn.close()
    assert dashboard["kpis"]["in_progress"]["value"] == 1
    assert dashboard["kpis"]["closings_due"]["value"] == 2
    assert dashboard["kpis"]["in_progress"]["value"] != dashboard["kpis"]["closings_due"]["value"]


def test_command_center_kpis_never_claim_en_route():
    """No KPI key, dashboard label, or explanation string anywhere in
    get_command_center()'s own source may claim 'en route' as a real,
    trackable fact -- en_route_count() exists and documents why that
    signal isn't real, but it must never be wired into the dashboard."""
    source = (REPO_ROOT / "src" / "dispatcher" / "kpi_read_model.py").read_text(encoding="utf-8")
    start = source.index("def get_command_center(")
    end = source.index("\ndef get_dashboard(")
    body = source[start:end]
    assert "en_route_count(conn" not in body  # documented-but-never-called is fine; an actual call is not

    js = (REPO_ROOT / "operator_ui_v2_connected" / "js" / "app.js").read_text(encoding="utf-8")
    assert "k.in_progress_en_route" not in js
    assert 'ccKpiCardHtml("In progress"' in js
