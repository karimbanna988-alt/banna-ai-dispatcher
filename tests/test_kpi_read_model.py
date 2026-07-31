"""Focused tests for src/dispatcher/kpi_read_model.py -- every test seeds
a temporary on-disk SQLite database via dispatcher.storage.connect(tmp_path)
(see conftest.py), never the real database. Synthetic data only, used
solely inside these isolated tests -- never written to any production
surface."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from dispatcher import kpi_read_model as kpi
from dispatcher import storage


@pytest.fixture()
def db(tmp_path):
    db_path = tmp_path / "kpi_test.db"
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


def _insert_event(conn, job_id, message_type, final_action=None, created_at=None,
                   closing_amount=None, confidence=None, update_type=None):
    conn.execute(
        "INSERT INTO events (job_id, ticket_number, raw_message, message_type, source, final_action, "
        "created_at, closing_amount, confidence, update_type) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (job_id, "T1", "raw", message_type, "AGN", final_action, created_at or _iso(datetime.now(timezone.utc)),
         closing_amount, confidence, update_type),
    )
    conn.commit()


def _insert_shadow_episode(conn, episode_id, source, review_status="UNREVIEWED", human_review_required=0, created_at=None):
    now = created_at or _iso(datetime.now(timezone.utc))
    conn.execute(
        "INSERT INTO shadow_episodes (episode_id, source, channel, review_status, human_review_required, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (episode_id, source, "quo", review_status, human_review_required, now, now),
    )
    conn.commit()


# ================================================================== EMPTY =
def test_empty_database_returns_zero_not_a_crash(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    start, end = kpi.resolve_period("24h", now, kpi.load_targets())
    result = kpi.jobs_received(conn, start, end)
    assert result["value"] == 0
    assert result["data_quality"] == "zero"
    conn.close()


def test_empty_database_dashboard_does_not_crash(db):
    conn = _conn(db)
    dash = kpi.get_dashboard(conn, period="24h")
    assert dash["summary"]["jobs_received"]["value"] == 0
    assert dash["attention_now"] == []
    conn.close()


# =============================================================== ONE JOB =
def test_one_job_sample_received_and_dispatch_rate(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    _insert_job(conn, "A1", "AGN", status="Approved", created_at=_iso(now - timedelta(minutes=10)))
    start, end = kpi.resolve_period("24h", now, kpi.load_targets())
    received = kpi.jobs_received(conn, start, end)
    assert received["value"] == 1
    dispatched = kpi.jobs_dispatched(conn, start, end)
    assert dispatched["value"] == 1
    conn.close()


# ===================================================== MISSING TIMESTAMPS
def test_missing_event_timestamp_excluded_from_duration_samples(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    job_id = _insert_job(conn, "A2", "AGN", created_at=_iso(now - timedelta(minutes=30)))
    conn.execute("INSERT INTO events (job_id, ticket_number, raw_message, message_type, source, created_at) VALUES (?,?,?,?,?,?)",
                 (job_id, "A2", "raw", "NEW", "AGN", ""))
    conn.commit()
    start, end = kpi.resolve_period("24h", now, kpi.load_targets())
    result = kpi.median_receive_to_review_minutes(conn, start, end, min_sample_size=1)
    assert result["data_quality"] == "insufficient_data"
    conn.close()


# ======================================================= DUPLICATE EVENTS
def test_duplicate_message_type_counted_in_ai_quality(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    then = now - timedelta(seconds=1)
    job_id = _insert_job(conn, "A3", "AGN", created_at=_iso(then))
    _insert_event(conn, job_id, "DUPLICATE", created_at=_iso(then))
    _insert_event(conn, job_id, "NEW", final_action="Approve", created_at=_iso(then))
    start, end = kpi.resolve_period("24h", now, kpi.load_targets())
    ai = kpi.ai_quality_summary(conn, start, end, min_sample_size=1)
    assert ai["duplicate_message_rate"]["numerator"] == 1
    assert ai["duplicate_message_rate"]["denominator"] == 2
    conn.close()


# ============================================================== CANCELLED
def test_cancelled_jobs_counted_in_cancellation_rate(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    then = now - timedelta(seconds=1)
    _insert_job(conn, "A4", "AGN", status="Cancelled", created_at=_iso(then))
    _insert_job(conn, "A5", "AGN", status="New", created_at=_iso(then))
    start, end = kpi.resolve_period("24h", now, kpi.load_targets())
    result = kpi.cancellation_rate(conn, start, end, min_sample_size=1)
    assert result["numerator"] == 1
    assert result["denominator"] == 2
    assert result["value"] == 50.0
    conn.close()


# ============================================================== COMPLETED
def test_completed_jobs_counted_by_status_closed(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    then = now - timedelta(seconds=1)
    _insert_job(conn, "A6", "AGN", status="Closed", created_at=_iso(then), updated_at=_iso(then))
    start, end = kpi.resolve_period("24h", now, kpi.load_targets())
    result = kpi.completed_jobs(conn, start, end)
    assert result["value"] == 1
    conn.close()


# ==================================================== MIDNIGHT CROSSING ==
def test_shift_period_crosses_midnight_correctly(db):
    conn = _conn(db)
    tz = __import__("zoneinfo").ZoneInfo("America/New_York")
    # 00:30 local time -- shift (since local midnight) should be only 30 min.
    now_local = datetime(2026, 3, 10, 0, 30, tzinfo=tz)
    now_utc = now_local.astimezone(timezone.utc)
    targets = kpi.load_targets()
    start, end = kpi.resolve_period("shift", now_utc, targets)
    assert (end - start) < timedelta(hours=1)
    assert (end - start) > timedelta(minutes=25)
    conn.close()


# ============================================ DAYLIGHT-SAVING BOUNDARIES =
def test_dst_spring_forward_boundary_does_not_crash_or_misbehave(db):
    conn = _conn(db)
    tz = __import__("zoneinfo").ZoneInfo("America/New_York")
    # 2026-03-08 is the US DST spring-forward date (2am -> 3am).
    now_local = datetime(2026, 3, 8, 10, 0, tzinfo=tz)
    now_utc = now_local.astimezone(timezone.utc)
    targets = kpi.load_targets()
    start, end = kpi.resolve_period("shift", now_utc, targets)
    assert start < end
    # A 24h period should still be exactly 24 real hours regardless of DST.
    start2, end2 = kpi.resolve_period("24h", now_utc, targets)
    assert (end2 - start2) == timedelta(hours=24)
    conn.close()


def test_dst_fall_back_boundary_does_not_crash(db):
    conn = _conn(db)
    tz = __import__("zoneinfo").ZoneInfo("America/New_York")
    # 2026-11-01 is the US DST fall-back date.
    now_local = datetime(2026, 11, 1, 10, 0, tzinfo=tz)
    now_utc = now_local.astimezone(timezone.utc)
    targets = kpi.load_targets()
    start, end = kpi.resolve_period("shift", now_utc, targets)
    assert start < end
    conn.close()


# ============================================================ ZERO DENOM =
def test_zero_denominator_never_produces_a_fabricated_percentage(db):
    result = kpi._rate_kpi(0, 0, "ok", "no data", min_sample=1)
    assert result["value"] is None
    assert result["data_quality"] == "insufficient_data"


def test_dispatch_rate_with_zero_jobs_received_is_insufficient_not_zero_percent(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    start, end = kpi.resolve_period("24h", now, kpi.load_targets())
    result = kpi.dispatch_rate(conn, start, end, min_sample_size=1)
    assert result["value"] is None
    assert result["data_quality"] in ("insufficient_data", "zero")
    conn.close()


# =================================================== INSUFFICIENT SAMPLE =
def test_below_min_sample_size_reports_insufficient_not_a_percentage(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    for i in range(2):
        _insert_job(conn, f"S{i}", "AGN", status="Closed", created_at=_iso(now), updated_at=_iso(now))
    start, end = kpi.resolve_period("24h", now, kpi.load_targets())
    result = kpi.closing_completion_rate(conn, start, end, min_sample_size=5)
    assert result["data_quality"] == "insufficient_data"
    assert result["value"] is None
    conn.close()


def test_ai_acceptance_rate_never_computed_from_unreviewed_episodes(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    for i in range(3):
        _insert_shadow_episode(conn, f"ep{i}", "AGN", review_status="UNREVIEWED", created_at=_iso(now))
    start, end = kpi.resolve_period("24h", now, kpi.load_targets())
    ai = kpi.ai_quality_summary(conn, start, end, min_sample_size=1)
    assert ai["acceptance_rate"]["data_quality"] == "insufficient_data"
    assert ai["acceptance_rate"]["value"] is None
    assert ai["acceptance_rate"]["denominator"] == 0
    conn.close()


def test_ai_acceptance_rate_computed_once_enough_reviewed_examples_exist(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    then = now - timedelta(seconds=1)
    for i in range(5):
        status = "VERIFIED" if i < 4 else "CORRECTED"
        _insert_shadow_episode(conn, f"ep{i}", "AGN", review_status=status, created_at=_iso(then))
    start, end = kpi.resolve_period("24h", now, kpi.load_targets())
    ai = kpi.ai_quality_summary(conn, start, end, min_sample_size=5)
    assert ai["acceptance_rate"]["data_quality"] == "ok"
    assert ai["acceptance_rate"]["value"] == 80.0
    conn.close()


# ============================================================== FILTERS ==
def test_source_filter_scopes_jobs_received(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    then = now - timedelta(seconds=1)
    _insert_job(conn, "F1", "AGN", created_at=_iso(then))
    _insert_job(conn, "F2", "Lucky", created_at=_iso(then))
    start, end = kpi.resolve_period("24h", now, kpi.load_targets())
    agn_only = kpi.jobs_received(conn, start, end, source="AGN")
    assert agn_only["value"] == 1
    conn.close()


def test_technician_filter_scopes_technician_summary(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    then = now - timedelta(seconds=1)
    tech_id = storage.add_technician(conn, name="Tech One", areas=["Charlotte"], skills=["car lockout"])
    other_tech_id = storage.add_technician(conn, name="Tech Two", areas=["Charlotte"], skills=["car lockout"])
    _insert_job(conn, "T1", "AGN", status="Closed", created_at=_iso(then), technician_id=tech_id)
    _insert_job(conn, "T2", "AGN", status="New", created_at=_iso(then), technician_id=other_tech_id)
    start, end = kpi.resolve_period("24h", now, kpi.load_targets())
    summary = kpi.technician_summary(conn, tech_id, start, end, min_sample_size=1)
    assert summary["jobs_assigned"]["value"] == 1
    assert summary["jobs_completed"]["value"] == 1
    conn.close()


def test_area_filter_scopes_jobs_received(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    then = now - timedelta(seconds=1)
    _insert_job(conn, "AR1", "AGN", created_at=_iso(then), city="Charlotte")
    _insert_job(conn, "AR2", "AGN", created_at=_iso(then), city="Raleigh")
    start, end = kpi.resolve_period("24h", now, kpi.load_targets())
    charlotte_only = kpi.jobs_received(conn, start, end, area="Charlotte")
    assert charlotte_only["value"] == 1
    conn.close()


# ============================================================ DATE RANGES
def test_custom_date_range_period(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=10)
    _insert_job(conn, "D1", "AGN", created_at=_iso(old))
    _insert_job(conn, "D2", "AGN", created_at=_iso(now))
    start, end = kpi.resolve_period("custom", now, kpi.load_targets(),
                                     custom_start=_iso(old - timedelta(hours=1)), custom_end=_iso(old + timedelta(hours=1)))
    result = kpi.jobs_received(conn, start, end)
    assert result["value"] == 1
    conn.close()


def test_7d_and_30d_periods_have_correct_width(db):
    now = datetime.now(timezone.utc)
    targets = kpi.load_targets()
    start7, end7 = kpi.resolve_period("7d", now, targets)
    assert (end7 - start7) == timedelta(days=7)
    start30, end30 = kpi.resolve_period("30d", now, targets)
    assert (end30 - start30) == timedelta(days=30)


# =================================================== MEDIAN / PERCENTILE =
def test_median_and_p90_correctness_known_distribution():
    # Known distribution: 1..10 minutes. Median of 1..10 = 5.5, p90 (linear interp) = 9.1.
    samples = list(range(1, 11))
    median, p90 = kpi._median_and_p90([float(x) for x in samples])
    assert median == 5.5
    assert p90 == 9.1


def test_median_single_sample():
    median, p90 = kpi._median_and_p90([42.0])
    assert median == 42.0
    assert p90 == 42.0


def test_median_empty_returns_none():
    median, p90 = kpi._median_and_p90([])
    assert median is None
    assert p90 is None


# ========================================================= STALE DETECTION
def test_stale_active_job_detected(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    targets = kpi.load_targets()
    stale_at = now - timedelta(minutes=targets["stale_target_minutes"] + 30)
    _insert_job(conn, "ST1", "AGN", status="In Progress", created_at=_iso(stale_at), last_event_at=_iso(stale_at))
    fresh_at = now - timedelta(minutes=5)
    _insert_job(conn, "ST2", "AGN", status="In Progress", created_at=_iso(fresh_at), last_event_at=_iso(fresh_at))
    result = kpi.active_jobs_without_recent_update(conn, now, targets)
    assert result["value"] == 1
    conn.close()


def test_attention_now_includes_stale_and_undispatched_jobs(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    targets = kpi.load_targets()
    old_new = now - timedelta(minutes=targets["dispatch_target_minutes"] + 10)
    _insert_job(conn, "AT1", "AGN", status="New", created_at=_iso(old_new))
    items = kpi.attention_now(conn, now, targets)
    assert any(i["type"] == "not_dispatched_within_target" for i in items)
    conn.close()


# ========================================================= CLOSING COMPLETE
def test_closing_completeness_requires_amount_and_payment_method(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    then = now - timedelta(seconds=1)
    tech_id = storage.add_technician(conn, name="Closer Tech", areas=["Charlotte"], skills=["car lockout"])
    job_id = _insert_job(conn, "CL1", "AGN", status="Closed", created_at=_iso(then), technician_id=tech_id)
    _insert_event(conn, job_id, "CLOSING", final_action="Approve", created_at=_iso(then), closing_amount="95")
    start, end = kpi.resolve_period("24h", now, kpi.load_targets())
    summary = kpi.technician_summary(conn, tech_id, start, end, min_sample_size=1)
    assert summary["closing_submission_completeness"]["numerator"] == 1
    conn.close()


# ============================================== REVIEWED VS UNREVIEWED AI
def test_unreviewed_episodes_excluded_from_correction_rate_denominator(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    then = now - timedelta(seconds=1)
    _insert_shadow_episode(conn, "u1", "AGN", review_status="UNREVIEWED", created_at=_iso(then))
    _insert_shadow_episode(conn, "u2", "AGN", review_status="CORRECTED", created_at=_iso(then))
    start, end = kpi.resolve_period("24h", now, kpi.load_targets())
    ai = kpi.ai_quality_summary(conn, start, end, min_sample_size=1)
    assert ai["correction_rate"]["denominator"] == 1
    conn.close()


# ================================================================ NO PII =
def test_dashboard_response_contains_no_phone_or_address_keys(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    _insert_job(conn, "PII1", "AGN", created_at=_iso(now))
    dash = kpi.get_dashboard(conn, period="24h", now=now)
    serialized = json.dumps(dash)
    assert "1-555-010-3000" not in serialized
    assert "100 Fake St" not in serialized
    assert "phone" not in serialized.lower()
    assert "address" not in serialized.lower()
    conn.close()


# ============================================================ READ-ONLY ==
def test_get_dashboard_never_writes_to_the_database(db):
    conn = _conn(db)
    now = datetime.now(timezone.utc)
    _insert_job(conn, "RO1", "AGN", created_at=_iso(now))
    conn.commit()
    before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    kpi.get_dashboard(conn, period="24h")
    after = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert before == after
    conn.close()


def test_kpi_read_model_module_contains_no_write_statements():
    import pathlib
    source = pathlib.Path("src/dispatcher/kpi_read_model.py").read_text(encoding="utf-8")
    for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP TABLE"):
        assert forbidden not in source


# ====================================================== NO FABRICATED UI =
def test_operator_api_performance_route_uses_real_db_only(monkeypatch, tmp_path):
    import operator_api as api
    db_path = tmp_path / "route_test.db"
    conn = storage.connect(db_path)
    conn.close()
    monkeypatch.setenv("OPERATOR_API_DB_PATH", str(db_path))
    import streamlit as st
    st.cache_resource.clear()
    result = api.route_performance(None, {}, {"period": "24h"})
    assert result["summary"]["jobs_received"]["value"] == 0  # real empty temp db, never fabricated


def test_operator_api_performance_route_rejects_bad_period(monkeypatch, tmp_path):
    import operator_api as api
    db_path = tmp_path / "route_test2.db"
    conn = storage.connect(db_path)
    conn.close()
    monkeypatch.setenv("OPERATOR_API_DB_PATH", str(db_path))
    import streamlit as st
    st.cache_resource.clear()
    with pytest.raises(api.ApiError):
        api.route_performance(None, {}, {"period": "not_a_real_period"})
