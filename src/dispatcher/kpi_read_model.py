"""Read-only KPI calculation engine for the Performance screen.

Every function here takes an existing sqlite3.Connection (never opens or
closes one itself) and returns plain dicts built from parameterized SQL
queries. This module:

- never writes to the database;
- never re-implements status transitions, parsing, routing, technician
  eligibility, or closing logic -- it only reads jobs.status (already set
  by storage.derive_job_status()) and events/shadow_episodes columns that
  already exist;
- never computes an AI "accuracy" figure from unreviewed episodes;
- never fabricates a value when the denominator is zero or the sample is
  too small -- see DataQuality below.

See docs/kpi_definitions.md for the exact, human-readable formula behind
every function here -- that document is the source of truth; this module
must match it, not the other way around.
"""

from __future__ import annotations

import pathlib
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_TARGETS_PATH = REPO_ROOT / "rules" / "kpi_targets.yaml"

DQ_OK = "ok"
DQ_ZERO = "zero"
DQ_INSUFFICIENT = "insufficient_data"
DQ_NEEDS_DEFINITION = "needs_business_definition"

_DEFAULT_TARGETS = {
    "dispatch_target_minutes": 30,
    "stale_target_minutes": 120,
    "min_sample_size": 5,
    "shift_start_local_time": "00:00",
    "operational_timezone": "America/New_York",
}


def load_targets() -> dict:
    """Loads rules/kpi_targets.yaml, falling back to documented defaults
    for any missing key. Never raises if the file is absent -- a missing
    config file is not a reason to crash the Performance screen."""
    targets = dict(_DEFAULT_TARGETS)
    try:
        with open(_TARGETS_PATH, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        targets.update({k: v for k, v in loaded.items() if k in _DEFAULT_TARGETS})
    except FileNotFoundError:
        pass
    return targets


def _iso_z(dt: datetime) -> str:
    """Formats a timezone-aware datetime to match this codebase's stored
    timestamp convention exactly (see tests/test_operator_api.py's
    future_ts() and every save_* call in storage.py): UTC, 'Z' suffix, not
    '+00:00'. Stored timestamps are compared as plain TEXT in SQLite (no
    datetime() cast), so this format must match exactly or range queries
    silently return wrong results -- Python's default .isoformat() (which
    emits '+00:00') does NOT match and must never be used as a SQL bound
    here."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Tolerant ISO-8601 parse of the codebase's stored UTC timestamps.
    Returns None for missing/unparseable values -- never raises, never
    guesses a fallback time."""
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def resolve_period(
    period: str,
    now_utc: datetime,
    targets: dict,
    custom_start: Optional[str] = None,
    custom_end: Optional[str] = None,
) -> tuple[datetime, datetime]:
    """Returns (start_utc, end_utc) for a named period. All boundary math
    happens in the configured operational time zone (DST-correct via
    zoneinfo), then converts to UTC -- never a fixed UTC offset."""
    tz = ZoneInfo(targets.get("operational_timezone", "America/New_York"))
    now_local = now_utc.astimezone(tz)

    if period == "shift":
        hh, mm = (targets.get("shift_start_local_time") or "00:00").split(":")
        shift_start_local = now_local.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if shift_start_local > now_local:
            shift_start_local -= timedelta(days=1)
        return shift_start_local.astimezone(timezone.utc), now_utc
    if period == "24h":
        return now_utc - timedelta(hours=24), now_utc
    if period == "7d":
        return now_utc - timedelta(days=7), now_utc
    if period == "30d":
        return now_utc - timedelta(days=30), now_utc
    if period == "custom":
        start = _parse_ts(custom_start)
        end = _parse_ts(custom_end)
        if start is None or end is None:
            raise ValueError("custom period requires valid custom_start and custom_end")
        return start, end
    raise ValueError(f"unknown period {period!r}")


def _kpi(value, numerator, denominator, sample_size, data_quality, explanation) -> dict:
    return {
        "value": value,
        "numerator": numerator,
        "denominator": denominator,
        "sample_size": sample_size,
        "data_quality": data_quality,
        "explanation": explanation,
    }


def _rate_kpi(numerator: int, denominator: int, explanation_ok: str, explanation_zero: str, min_sample: int = 1) -> dict:
    if denominator < min_sample:
        return _kpi(None, numerator, denominator, denominator, DQ_INSUFFICIENT, "Insufficient data for this period.")
    if denominator == 0:
        return _kpi(None, numerator, denominator, denominator, DQ_ZERO, explanation_zero)
    value = round(numerator / denominator * 100, 1)
    return _kpi(value, numerator, denominator, denominator, DQ_OK, explanation_ok)


def _median_and_p90(durations_minutes: list) -> tuple[Optional[float], Optional[float]]:
    if not durations_minutes:
        return None, None
    ordered = sorted(durations_minutes)
    median = round(statistics.median(ordered), 1)
    if len(ordered) == 1:
        p90 = round(ordered[0], 1)
    else:
        # Linear-interpolation percentile, matching numpy's default method.
        rank = 0.9 * (len(ordered) - 1)
        lo = int(rank)
        hi = min(lo + 1, len(ordered) - 1)
        frac = rank - lo
        p90 = round(ordered[lo] + (ordered[hi] - ordered[lo]) * frac, 1)
    return median, p90


def _area_clause(alias: str = "j") -> str:
    return f"json_extract({alias}.latest_fields_json, '$.city')"


def _build_job_filters(source: Optional[str], area: Optional[str], status_in: Optional[list] = None) -> tuple[str, list]:
    clauses, params = [], []
    if source:
        clauses.append("j.source = ?")
        params.append(source)
    if area:
        clauses.append(f"{_area_clause()} = ?")
        params.append(area)
    if status_in:
        placeholders = ",".join("?" for _ in status_in)
        clauses.append(f"j.status IN ({placeholders})")
        params.extend(status_in)
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


# ============================================================ CORE OPS ===
def jobs_received(conn: sqlite3.Connection, start: datetime, end: datetime, source=None, area=None) -> dict:
    where, params = _build_job_filters(source, area)
    sql = f"SELECT COUNT(*) FROM jobs j WHERE j.created_at >= ? AND j.created_at < ?{where}"
    n = conn.execute(sql, [_iso_z(start), _iso_z(end), *params]).fetchone()[0]
    return _kpi(n, n, n, n, DQ_OK if n > 0 else DQ_ZERO, f"{n} job(s) received in this period.")


def new_jobs_by_source(conn: sqlite3.Connection, start: datetime, end: datetime, area=None) -> dict:
    where, params = _build_job_filters(None, area)
    sql = f"SELECT j.source, COUNT(*) FROM jobs j WHERE j.created_at >= ? AND j.created_at < ?{where} GROUP BY j.source ORDER BY 2 DESC"
    rows = conn.execute(sql, [_iso_z(start), _iso_z(end), *params]).fetchall()
    return {source: count for source, count in rows}


def new_jobs_by_area(conn: sqlite3.Connection, start: datetime, end: datetime, source=None) -> dict:
    where, params = _build_job_filters(source, None)
    sql = f"SELECT {_area_clause()}, COUNT(*) FROM jobs j WHERE j.created_at >= ? AND j.created_at < ?{where} GROUP BY 1 ORDER BY 2 DESC"
    rows = conn.execute(sql, [_iso_z(start), _iso_z(end), *params]).fetchall()
    return {(area or "Unknown"): count for area, count in rows}


_DISPATCHED_STATUSES = ("Approved", "In Progress", "Closed")


def jobs_dispatched(conn: sqlite3.Connection, start: datetime, end: datetime, source=None, area=None) -> dict:
    where, params = _build_job_filters(source, area, status_in=list(_DISPATCHED_STATUSES))
    sql = f"SELECT COUNT(*) FROM jobs j WHERE j.created_at >= ? AND j.created_at < ?{where}"
    n = conn.execute(sql, [_iso_z(start), _iso_z(end), *params]).fetchone()[0]
    return _kpi(n, n, n, n, DQ_OK if n > 0 else DQ_ZERO, f"{n} job(s) dispatched in this period.")


def dispatch_rate(conn: sqlite3.Connection, start: datetime, end: datetime, source=None, area=None, min_sample_size: int = 1) -> dict:
    received = jobs_received(conn, start, end, source, area)["numerator"]
    dispatched = jobs_dispatched(conn, start, end, source, area)["numerator"]
    return _rate_kpi(dispatched, received, "Share of received jobs that were dispatched.", "No jobs received in this period.", min_sample_size)


def _job_duration_samples(conn: sqlite3.Connection, start: datetime, end: datetime, source, area, dispatch_only: bool) -> list:
    where, params = _build_job_filters(source, area)
    sql = f"""
        SELECT j.id, j.created_at,
               (SELECT MIN(e.created_at) FROM events e
                WHERE e.job_id = j.id
                {"AND e.message_type = 'NEW' AND e.final_action IN ('Approve','Edit')" if dispatch_only else ""})
        FROM jobs j
        WHERE j.created_at >= ? AND j.created_at < ?{where}
    """
    rows = conn.execute(sql, [_iso_z(start), _iso_z(end), *params]).fetchall()
    samples = []
    for _id, created_at, first_event_at in rows:
        created = _parse_ts(created_at)
        first_event = _parse_ts(first_event_at)
        if created and first_event and first_event >= created:
            samples.append((first_event - created).total_seconds() / 60.0)
    return samples


def median_receive_to_review_minutes(conn, start, end, source=None, area=None, min_sample_size: int = 1) -> dict:
    samples = _job_duration_samples(conn, start, end, source, area, dispatch_only=False)
    median, _p90 = _median_and_p90(samples)
    if len(samples) < min_sample_size:
        return _kpi(None, len(samples), len(samples), len(samples), DQ_INSUFFICIENT, "Insufficient reviewed jobs for this period.")
    return _kpi(median, len(samples), len(samples), len(samples), DQ_OK, "Median minutes from job received to first event.")


def median_receive_to_dispatch_minutes(conn, start, end, source=None, area=None, min_sample_size: int = 1) -> dict:
    samples = _job_duration_samples(conn, start, end, source, area, dispatch_only=True)
    median, _p90 = _median_and_p90(samples)
    if len(samples) < min_sample_size:
        return _kpi(None, len(samples), len(samples), len(samples), DQ_INSUFFICIENT, "Insufficient dispatched jobs for this period.")
    return _kpi(median, len(samples), len(samples), len(samples), DQ_OK, "Median minutes from job received to dispatch.")


def p90_receive_to_dispatch_minutes(conn, start, end, source=None, area=None, min_sample_size: int = 1) -> dict:
    samples = _job_duration_samples(conn, start, end, source, area, dispatch_only=True)
    _median, p90 = _median_and_p90(samples)
    if len(samples) < min_sample_size:
        return _kpi(None, len(samples), len(samples), len(samples), DQ_INSUFFICIENT, "Insufficient dispatched jobs for this period.")
    return _kpi(p90, len(samples), len(samples), len(samples), DQ_OK, "90th-percentile minutes from job received to dispatch.")


def jobs_waiting_over_target(conn: sqlite3.Connection, now: datetime, targets: dict, source=None, area=None) -> dict:
    """Current snapshot (not period-bound): New/Needs-Review jobs older
    than the configured dispatch target."""
    target_minutes = targets["dispatch_target_minutes"]
    where, params = _build_job_filters(source, area, status_in=["New", "Needs Review"])
    sql = f"SELECT j.created_at FROM jobs j WHERE 1=1{where}"
    rows = conn.execute(sql, params).fetchall()
    n = 0
    for (created_at,) in rows:
        created = _parse_ts(created_at)
        if created and (now - created).total_seconds() / 60.0 > target_minutes:
            n += 1
    return _kpi(n, n, len(rows), len(rows), DQ_OK if rows else DQ_ZERO, f"{n} job(s) waiting more than {target_minutes} minutes.")


def active_jobs_without_recent_update(conn: sqlite3.Connection, now: datetime, targets: dict, source=None, area=None) -> dict:
    target_minutes = targets["stale_target_minutes"]
    where, params = _build_job_filters(source, area, status_in=["Approved", "In Progress"])
    sql = f"SELECT j.last_event_at, j.updated_at FROM jobs j WHERE 1=1{where}"
    rows = conn.execute(sql, params).fetchall()
    n = 0
    for last_event_at, updated_at in rows:
        reference = _parse_ts(last_event_at) or _parse_ts(updated_at)
        if reference and (now - reference).total_seconds() / 60.0 > target_minutes:
            n += 1
    return _kpi(n, n, len(rows), len(rows), DQ_OK if rows else DQ_ZERO, f"{n} active job(s) with no update in over {target_minutes} minutes.")


def completed_jobs(conn: sqlite3.Connection, start: datetime, end: datetime, source=None, area=None) -> dict:
    where, params = _build_job_filters(source, area, status_in=["Closed"])
    sql = f"SELECT COUNT(*) FROM jobs j WHERE j.updated_at >= ? AND j.updated_at < ?{where}"
    n = conn.execute(sql, [_iso_z(start), _iso_z(end), *params]).fetchone()[0]
    return _kpi(n, n, n, n, DQ_OK if n > 0 else DQ_ZERO, f"{n} job(s) completed in this period.")


def cancellation_rate(conn, start, end, source=None, area=None, min_sample_size: int = 1) -> dict:
    received = jobs_received(conn, start, end, source, area)["numerator"]
    where, params = _build_job_filters(source, area, status_in=["Cancelled"])
    sql = f"SELECT COUNT(*) FROM jobs j WHERE j.created_at >= ? AND j.created_at < ?{where}"
    cancelled = conn.execute(sql, [_iso_z(start), _iso_z(end), *params]).fetchone()[0]
    return _rate_kpi(cancelled, received, "Share of received jobs cancelled.", "No jobs received in this period.", min_sample_size)


def pass_manual_handle_rate(conn, start, end, source=None, area=None, min_sample_size: int = 1) -> dict:
    received = jobs_received(conn, start, end, source, area)["numerator"]
    where, params = _build_job_filters(source, area, status_in=["Handle Manually"])
    sql = f"SELECT COUNT(*) FROM jobs j WHERE j.created_at >= ? AND j.created_at < ?{where}"
    passed = conn.execute(sql, [_iso_z(start), _iso_z(end), *params]).fetchone()[0]
    return _rate_kpi(passed, received, "Share of received jobs passed / handled manually.", "No jobs received in this period.", min_sample_size)


def active_jobs_count(conn: sqlite3.Connection, source=None, area=None) -> dict:
    """Current snapshot: jobs in New/Approved/In Progress right now."""
    where, params = _build_job_filters(source, area, status_in=["New", "Approved", "In Progress"])
    n = conn.execute(f"SELECT COUNT(*) FROM jobs j WHERE 1=1{where}", params).fetchone()[0]
    return _kpi(n, n, n, n, DQ_OK if n > 0 else DQ_ZERO, f"{n} active job(s) right now.")


def closings_due_count(conn: sqlite3.Connection, source=None, area=None) -> dict:
    """Current snapshot: dispatched jobs (In Progress) still awaiting a
    closing -- the same population the Closings screen surfaces."""
    where, params = _build_job_filters(source, area, status_in=["In Progress"])
    n = conn.execute(f"SELECT COUNT(*) FROM jobs j WHERE 1=1{where}", params).fetchone()[0]
    return _kpi(n, n, n, n, DQ_OK if n > 0 else DQ_ZERO, f"{n} closing(s) due right now.")


def closing_completion_rate(conn, start, end, source=None, area=None, min_sample_size: int = 1) -> dict:
    dispatched = jobs_dispatched(conn, start, end, source, area)["numerator"]
    closed = completed_jobs(conn, start, end, source, area)["numerator"]
    return _rate_kpi(closed, dispatched, "Share of dispatched jobs that closed.", "No jobs dispatched in this period.", min_sample_size)


# =========================================================== TECHNICIAN ==
def technician_summary(conn: sqlite3.Connection, technician_id: int, start: datetime, end: datetime, min_sample_size: int = 1) -> dict:
    rows = conn.execute(
        "SELECT j.id, j.status, "
        "(SELECT e.closing_amount FROM events e WHERE e.job_id = j.id AND e.closing_amount IS NOT NULL "
        "ORDER BY e.id DESC LIMIT 1) AS last_closing_amount "
        "FROM jobs j WHERE j.assigned_technician_id = ? AND j.created_at >= ? AND j.created_at < ?",
        [technician_id, _iso_z(start), _iso_z(end)],
    ).fetchall()
    assigned = len(rows)
    accepted = sum(1 for _id, status, _c in rows if status in ("In Progress", "Closed"))
    passed = sum(1 for _id, status, _c in rows if status == "Handle Manually")
    completed = sum(1 for _id, status, _c in rows if status == "Closed")
    closed_with_amount = sum(1 for _id, status, c in rows if status == "Closed" and c)

    lacking_updates = 0
    for job_id, _status, _c in rows:
        has_status_event = conn.execute(
            "SELECT 1 FROM events WHERE job_id = ? AND message_type = 'STATUS' LIMIT 1", [job_id]
        ).fetchone()
        if not has_status_event:
            lacking_updates += 1

    return {
        "jobs_assigned": _kpi(assigned, assigned, assigned, assigned, DQ_OK if assigned else DQ_ZERO, f"{assigned} job(s) assigned."),
        "jobs_accepted_or_progressed": _kpi(accepted, accepted, assigned, assigned, DQ_OK if assigned else DQ_ZERO, "Jobs that reached In Progress or Closed."),
        "jobs_passed": _kpi(passed, passed, assigned, assigned, DQ_OK if assigned else DQ_ZERO, "Jobs passed / handled manually."),
        "jobs_completed": _kpi(completed, completed, assigned, assigned, DQ_OK if assigned else DQ_ZERO, "Jobs closed."),
        "completion_rate": _rate_kpi(completed, assigned, "Share of assigned jobs completed.", "No jobs assigned in this period.", min_sample_size),
        "jobs_lacking_required_updates": _kpi(lacking_updates, lacking_updates, assigned, assigned, DQ_OK if assigned else DQ_ZERO, "Assigned jobs with zero recorded STATUS updates."),
        "closing_submission_completeness": _rate_kpi(closed_with_amount, completed, "Share of completed jobs with a recorded closing amount.", "No jobs completed in this period.", min_sample_size),
        "no_answer_no_show_counts": _kpi(None, None, None, None, DQ_NEEDS_DEFINITION, "No update_type value maps to this concept yet -- needs a business definition."),
        "average_closed_amount": _kpi(None, None, None, None, DQ_INSUFFICIENT, "No numeric closing amounts recorded yet."),
    }


def technicians_summary(conn: sqlite3.Connection, start: datetime, end: datetime, min_sample_size: int = 1) -> list:
    """One efficient query across all technicians (never N+1) -- name,
    jobs assigned/completed, and completion rate for the period. Sorted
    by jobs_assigned descending; the frontend caps the initial view to 5
    rows, matching the mobile card-cap convention used elsewhere."""
    rows = conn.execute(
        "SELECT t.id, t.name, "
        "COUNT(j.id) AS assigned, "
        "SUM(CASE WHEN j.status = 'Closed' THEN 1 ELSE 0 END) AS completed "
        "FROM technicians t LEFT JOIN jobs j "
        "ON j.assigned_technician_id = t.id AND j.created_at >= ? AND j.created_at < ? "
        "GROUP BY t.id, t.name "
        "ORDER BY assigned DESC, t.name ASC",
        [_iso_z(start), _iso_z(end)],
    ).fetchall()
    result = []
    for technician_id, name, assigned, completed in rows:
        assigned = assigned or 0
        completed = completed or 0
        result.append({
            "technician_id": technician_id,
            "name": name,
            "jobs_assigned": assigned,
            "jobs_completed": completed,
            "completion_rate": _rate_kpi(completed, assigned, "Share of assigned jobs completed.", "No jobs assigned in this period.", min_sample_size),
        })
    return result


# =============================================================== SOURCE ==
def distinct_sources(conn: sqlite3.Connection) -> list:
    return [row[0] for row in conn.execute("SELECT DISTINCT source FROM jobs ORDER BY source").fetchall()]


def source_summary(conn: sqlite3.Connection, source: str, start: datetime, end: datetime, min_sample_size: int = 1) -> dict:
    received = jobs_received(conn, start, end, source=source)
    return {
        "jobs_received": received,
        "dispatch_rate": dispatch_rate(conn, start, end, source=source, min_sample_size=min_sample_size),
        "completion_rate": closing_completion_rate(conn, start, end, source=source, min_sample_size=min_sample_size),
        "cancellation_rate": cancellation_rate(conn, start, end, source=source, min_sample_size=min_sample_size),
        "median_dispatch_minutes": median_receive_to_dispatch_minutes(conn, start, end, source=source, min_sample_size=min_sample_size),
        "manual_review_rate": pass_manual_handle_rate(conn, start, end, source=source, min_sample_size=min_sample_size),
        "average_reliable_closing_amount": _kpi(None, None, None, None, DQ_INSUFFICIENT, "No numeric closing amounts recorded yet."),
    }


# ============================================================ AI QUALITY =
def ai_quality_summary(conn: sqlite3.Connection, start: datetime, end: datetime, min_sample_size: int) -> dict:
    episodes = conn.execute(
        "SELECT review_status, human_review_required, source FROM shadow_episodes "
        "WHERE created_at >= ? AND created_at < ?",
        [_iso_z(start), _iso_z(end)],
    ).fetchall()
    total = len(episodes)
    reviewed = [e for e in episodes if e[0] != "UNREVIEWED"]
    verified = sum(1 for e in reviewed if e[0] == "VERIFIED")
    corrected = sum(1 for e in reviewed if e[0] == "CORRECTED")
    excluded = sum(1 for e in episodes if e[0] in ("EXCLUDE", "SENSITIVE", "INCOMPLETE"))
    human_review_flagged = sum(1 for e in episodes if e[1])

    events = conn.execute(
        "SELECT message_type, confidence FROM events WHERE created_at >= ? AND created_at < ?",
        [_iso_z(start), _iso_z(end)],
    ).fetchall()
    total_events = len(events)
    unrecognized = sum(1 for mt, _c in events if mt == "UNRECOGNIZED")
    duplicate = sum(1 for mt, _c in events if mt == "DUPLICATE")

    buckets = {"0-39": 0, "40-69": 0, "70-89": 0, "90-100": 0}
    for _mt, confidence in events:
        if confidence is None:
            continue
        if confidence < 40:
            buckets["0-39"] += 1
        elif confidence < 70:
            buckets["40-69"] += 1
        elif confidence < 90:
            buckets["70-89"] += 1
        else:
            buckets["90-100"] += 1

    return {
        "human_review_required_rate": _rate_kpi(human_review_flagged, total, "Share of episodes the AI flagged for human review.", "No Shadow episodes in this period.", min_sample=1),
        "acceptance_rate": _rate_kpi(verified, len(reviewed), "Share of reviewed suggestions verified as correct.", "No reviewed episodes in this period.", min_sample_size),
        "correction_rate": _rate_kpi(corrected, len(reviewed), "Share of reviewed suggestions that needed correction.", "No reviewed episodes in this period.", min_sample_size),
        "excluded_sensitive_incomplete_count": _kpi(excluded, excluded, total, total, DQ_OK if total else DQ_ZERO, "Episodes excluded, sensitive, or incomplete."),
        "parser_confidence_distribution": buckets,
        "unrecognized_message_rate": _rate_kpi(unrecognized, total_events, "Share of events the parser could not recognize.", "No events in this period.", min_sample=1),
        "duplicate_message_rate": _rate_kpi(duplicate, total_events, "Share of events that were duplicates.", "No events in this period.", min_sample=1),
    }


def ai_quality_accuracy_by_source(conn: sqlite3.Connection, start: datetime, end: datetime, min_sample_size: int) -> dict:
    result = {}
    for source in distinct_sources(conn):
        rows = conn.execute(
            "SELECT review_status FROM shadow_episodes WHERE source = ? AND created_at >= ? AND created_at < ? AND review_status != 'UNREVIEWED'",
            [source, _iso_z(start), _iso_z(end)],
        ).fetchall()
        verified = sum(1 for (status,) in rows if status == "VERIFIED")
        result[source] = _rate_kpi(verified, len(rows), "Share of reviewed suggestions verified as correct.", "No reviewed episodes for this source in this period.", min_sample_size)
    return result


# ========================================================= ATTENTION NOW =
def attention_now(conn: sqlite3.Connection, now: datetime, targets: dict) -> list:
    """Operational exceptions only -- each item includes enough to deep-
    link to the related job. Never fabricates an item; every row traces
    to a real jobs/shadow_episodes row."""
    items = []
    dispatch_target = targets["dispatch_target_minutes"]
    stale_target = targets["stale_target_minutes"]

    for job_id, ticket, source, created_at in conn.execute(
        "SELECT id, ticket_number, source, created_at FROM jobs WHERE status IN ('New','Needs Review')"
    ).fetchall():
        created = _parse_ts(created_at)
        if created and (now - created).total_seconds() / 60.0 > dispatch_target:
            items.append({"type": "not_dispatched_within_target", "job_id": job_id, "ticket": ticket, "source": source, "primary_action": "Review job"})

    for job_id, ticket, source, last_event_at, updated_at in conn.execute(
        "SELECT id, ticket_number, source, last_event_at, updated_at FROM jobs WHERE status IN ('Approved','In Progress')"
    ).fetchall():
        reference = _parse_ts(last_event_at) or _parse_ts(updated_at)
        if reference and (now - reference).total_seconds() / 60.0 > stale_target:
            items.append({"type": "stale_active_job", "job_id": job_id, "ticket": ticket, "source": source, "primary_action": "Add update"})

    return items


# ============================================================== DASHBOARD
def _parse_numeric_amount(raw: Optional[str]) -> Optional[float]:
    """Strips $ and commas and parses as a plain number -- the same rule
    documented for average_closed_amount/closing_submission_completeness
    above. Returns None (never 0) for missing or non-numeric text."""
    if not raw:
        return None
    cleaned = raw.strip().replace("$", "").replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def new_jobs_recent(conn: sqlite3.Connection, now: datetime, window_minutes: int = 60, source=None, area=None) -> dict:
    """Current snapshot: status='New' jobs received within the last
    window_minutes. Distinct from jobs_received(), which counts every
    status created in a period -- this is the Command Center's RECENT
    NEW definition (see docs/command_center.md)."""
    where, params = _build_job_filters(source, area, status_in=["New"])
    rows = conn.execute(f"SELECT created_at FROM jobs j WHERE 1=1{where}", params).fetchall()
    n = 0
    for (created_at,) in rows:
        created = _parse_ts(created_at)
        if created and (now - created).total_seconds() / 60.0 <= window_minutes:
            n += 1
    return _kpi(n, n, len(rows), len(rows), DQ_OK if rows else DQ_ZERO, f"{n} new job(s) received in the last {window_minutes} minutes.")


def active_with_technician_count(conn: sqlite3.Connection, source=None, area=None) -> dict:
    """Current snapshot: dispatched jobs (Approved/In Progress) that have
    a real assigned technician -- the Command Center's ACTIVE definition.
    Distinct from active_jobs_count() (Performance screen), which also
    includes not-yet-dispatched New jobs and does not require a
    technician assignment -- that is a different, already-relied-upon
    question and is left unchanged."""
    where, params = _build_job_filters(source, area, status_in=["Approved", "In Progress"])
    sql = f"SELECT COUNT(*) FROM jobs j WHERE j.assigned_technician_id IS NOT NULL{where}"
    n = conn.execute(sql, params).fetchone()[0]
    return _kpi(n, n, n, n, DQ_OK if n > 0 else DQ_ZERO, f"{n} active job(s) with a technician assigned.")


def en_route_count(conn: sqlite3.Connection) -> dict:
    """No update_type value in the real schema reliably distinguishes
    'en route' from any other technician status update yet -- same
    needs_business_definition posture as technician_summary()'s
    no_answer_no_show_counts. Never fabricated as a guessed subset of
    active jobs."""
    return _kpi(None, None, None, None, DQ_NEEDS_DEFINITION, "No reliable 'en route' signal exists in the real data yet -- needs a dedicated technician update_type value.")


def verified_closed_amount(conn: sqlite3.Connection, start: datetime, end: datetime, source=None, area=None) -> dict:
    """Sum of closing_amount values that parse as a plain number, for
    jobs that closed in this period. Only jobs with a reliable numeric
    amount are summed; jobs missing one are counted (denominator) but
    excluded from the total, never coerced to 0 -- same parsing
    convention as the technician 'average closed amount' KPI above."""
    where, params = _build_job_filters(source, area, status_in=["Closed"])
    sql = f"SELECT j.id FROM jobs j WHERE j.updated_at >= ? AND j.updated_at < ?{where}"
    job_ids = [r[0] for r in conn.execute(sql, [_iso_z(start), _iso_z(end), *params]).fetchall()]
    if not job_ids:
        return _kpi(0.0, 0, 0, 0, DQ_ZERO, "No jobs closed in this period.")
    total = 0.0
    reliable = 0
    for job_id in job_ids:
        row = conn.execute(
            "SELECT closing_amount FROM events WHERE job_id = ? AND closing_amount IS NOT NULL ORDER BY id DESC LIMIT 1",
            [job_id],
        ).fetchone()
        amount = _parse_numeric_amount(row[0]) if row else None
        if amount is not None:
            total += amount
            reliable += 1
    if reliable < len(job_ids):
        return _kpi(round(total, 2), reliable, len(job_ids), len(job_ids), DQ_INSUFFICIENT, "Closing data incomplete -- some closed jobs have no reliable numeric amount.")
    return _kpi(round(total, 2), reliable, len(job_ids), len(job_ids), DQ_OK, f"${total:,.2f} verified across {reliable} closed job(s).")


def job_pipeline_counts(conn: sqlite3.Connection, period_start: datetime, period_end: datetime, now: datetime, targets: dict, source=None, area=None) -> dict:
    """Mutually-exclusive pipeline-stage snapshot for the Command
    Center's pipeline chart. New/Waiting/Approved-Assigned/In-Progress-
    are-Closing-Due are current-state snapshots (a job that's still open
    isn't naturally period-bound, matching jobs_waiting_over_target/
    closings_due_count's own snapshot semantics); Closed/Cancelled are
    scoped to the selected period so the chart doesn't accumulate
    all-time history. "In Progress" and "Closing Due" are intentionally
    ONE bucket, not two: in the real schema, status='In Progress' IS the
    exact population closings_due_count() already surfaces (a job
    reaching In Progress is simultaneously "being worked" and "awaiting
    its closing") -- there is no real field distinguishing "actively en
    route" from "closing due" within that status, and inventing one
    would violate "do not create new database statuses". No fabricated
    'En route'-only bucket -- see en_route_count()."""
    dispatch_target = targets["dispatch_target_minutes"]
    where, params = _build_job_filters(source, area, status_in=["New", "Needs Review"])
    rows = conn.execute(f"SELECT created_at FROM jobs j WHERE 1=1{where}", params).fetchall()
    new_count = 0
    waiting_count = 0
    for (created_at,) in rows:
        created = _parse_ts(created_at)
        if created and (now - created).total_seconds() / 60.0 > dispatch_target:
            waiting_count += 1
        else:
            new_count += 1

    assigned_where, assigned_params = _build_job_filters(source, area, status_in=["Approved"])
    assigned_count = conn.execute(f"SELECT COUNT(*) FROM jobs j WHERE 1=1{assigned_where}", assigned_params).fetchone()[0]

    in_progress_closing_due = closings_due_count(conn, source, area)["value"]
    closed_in_period = completed_jobs(conn, period_start, period_end, source, area)["value"]

    cancelled_where, cancelled_params = _build_job_filters(source, area, status_in=["Cancelled"])
    cancelled_sql = f"SELECT COUNT(*) FROM jobs j WHERE j.created_at >= ? AND j.created_at < ?{cancelled_where}"
    cancelled_in_period = conn.execute(cancelled_sql, [_iso_z(period_start), _iso_z(period_end), *cancelled_params]).fetchone()[0]

    return {
        "New": new_count,
        "Waiting": waiting_count,
        "Approved / Assigned": assigned_count,
        "In Progress / Closing Due": in_progress_closing_due,
        "Closed": closed_in_period,
        "Cancelled": cancelled_in_period,
    }


def _attention_now_with_age(conn: sqlite3.Connection, now: datetime, targets: dict) -> list:
    """attention_now(), enriched with age_minutes, sorted oldest-first
    (most urgent first) and THEN capped to 5 -- so the 5 shown are truly
    the most urgent, not just the first 5 attention_now() happened to
    return. A small additive lookup local to this function only, so
    attention_now()'s own return shape (and the Performance screen/tests
    that already depend on it) stays unchanged."""
    items = attention_now(conn, now, targets)
    for item in items:
        row = conn.execute("SELECT created_at, last_event_at, updated_at FROM jobs WHERE id = ?", [item["job_id"]]).fetchone()
        age_minutes = None
        if row:
            created_at, last_event_at, updated_at = row
            reference = _parse_ts(last_event_at) or _parse_ts(updated_at) or _parse_ts(created_at)
            if reference:
                age_minutes = max(0, int((now - reference).total_seconds() // 60))
        item["age_minutes"] = age_minutes
    items.sort(key=lambda i: i["age_minutes"] if i["age_minutes"] is not None else -1, reverse=True)
    return items[:5]


COMMAND_CENTER_CHART_PERIODS = ("shift", "today", "7d", "30d")


def get_command_center(
    conn: sqlite3.Connection, now: Optional[datetime] = None, source: Optional[str] = None,
    area: Optional[str] = None, chart_period: str = "today",
) -> dict:
    """Single orchestrator for the boss-facing Command Center screen.
    Every metric below is either an existing function from this module
    called as-is, or a small new function defined immediately above --
    nothing here re-implements SQL or business logic that already exists
    elsewhere in this file. See docs/command_center.md for the exact
    definition behind every field.

    The top KPI cards (Part 3 of the phase brief) are always "right
    now"/"today" -- they never move with chart_period, matching the
    brief's literal wording ("Completed today", "Verified closed amount
    today"). Only the charts section (Part 6) honors chart_period, one
    of "today"/"shift" (both = today so far, America/New_York),
    "7d" (last 7 days), "30d" (last 30 days)."""
    targets = load_targets()
    now = now or datetime.now(timezone.utc)
    today_start, today_end = resolve_period("shift", now, targets)
    if chart_period not in COMMAND_CENTER_CHART_PERIODS:
        raise ValueError(f"chart_period must be one of {COMMAND_CENTER_CHART_PERIODS}")
    chart_start, chart_end = resolve_period("shift" if chart_period in ("today", "shift") else chart_period, now, targets)
    min_sample = targets["min_sample_size"]

    # "In progress / en route" and "Closings due" intentionally share one
    # real number (closings_due_count() = status='In Progress'): the real
    # schema has no field distinguishing "actively en route" from
    # "closing due" within that status -- see en_route_count()'s own
    # docstring for why that distinction is not fabricated. Both cards
    # are honest about the same real population, not a bug.
    in_progress = closings_due_count(conn, source, area)

    return {
        "generated_at": _iso_z(now),
        "period": {
            "today_start": today_start.isoformat(), "today_end": today_end.isoformat(),
            "chart_period": chart_period, "chart_start": chart_start.isoformat(), "chart_end": chart_end.isoformat(),
        },
        "targets": targets,
        "kpis": {
            "new_last_60_minutes": new_jobs_recent(conn, now, window_minutes=60, source=source, area=area),
            "waiting_for_assignment": jobs_waiting_over_target(conn, now, targets, source, area),
            "active_with_technician": active_with_technician_count(conn, source, area),
            "in_progress_en_route": in_progress,
            "closings_due": closings_due_count(conn, source, area),
            "completed_today": completed_jobs(conn, today_start, today_end, source, area),
        },
        "verified_closed_amount_today": verified_closed_amount(conn, today_start, today_end, source, area),
        "attention_now": _attention_now_with_age(conn, now, targets),
        "charts": {
            "job_source_mix": new_jobs_by_source(conn, chart_start, chart_end, area),
            "job_pipeline": job_pipeline_counts(conn, chart_start, chart_end, now, targets, source, area),
            "technician_activity": [
                t for t in technicians_summary(conn, chart_start, chart_end, min_sample) if t["jobs_assigned"] > 0
            ],
            "verified_closings": verified_closed_amount(conn, chart_start, chart_end, source, area),
        },
    }


def get_dashboard(conn: sqlite3.Connection, period: str, now: Optional[datetime] = None, source: Optional[str] = None, area: Optional[str] = None, custom_start=None, custom_end=None) -> dict:
    """Single orchestrator the API route calls. Combines every section
    above for the mobile Performance screen."""
    targets = load_targets()
    now = now or datetime.now(timezone.utc)
    start, end = resolve_period(period, now, targets, custom_start, custom_end)
    min_sample = targets["min_sample_size"]

    return {
        "period": {"name": period, "start": start.isoformat(), "end": end.isoformat()},
        "targets": targets,
        "summary": {
            "jobs_received": jobs_received(conn, start, end, source, area),
            "jobs_waiting": jobs_waiting_over_target(conn, now, targets, source, area),
            "active_jobs": active_jobs_count(conn, source, area),
            "closings_due": closings_due_count(conn, source, area),
            "active_jobs_stale": active_jobs_without_recent_update(conn, now, targets, source, area),
            "median_dispatch_minutes": median_receive_to_dispatch_minutes(conn, start, end, source, area, min_sample),
            "completion_rate": closing_completion_rate(conn, start, end, source, area, min_sample),
        },
        "attention_now": attention_now(conn, now, targets),
        "operations": {
            "jobs_received": jobs_received(conn, start, end, source, area),
            "new_jobs_by_source": new_jobs_by_source(conn, start, end, area),
            "new_jobs_by_area": new_jobs_by_area(conn, start, end, source),
            "jobs_dispatched": jobs_dispatched(conn, start, end, source, area),
            "dispatch_rate": dispatch_rate(conn, start, end, source, area, min_sample),
            "median_receive_to_review_minutes": median_receive_to_review_minutes(conn, start, end, source, area, min_sample),
            "median_receive_to_dispatch_minutes": median_receive_to_dispatch_minutes(conn, start, end, source, area, min_sample),
            "p90_receive_to_dispatch_minutes": p90_receive_to_dispatch_minutes(conn, start, end, source, area, min_sample),
            "completed_jobs": completed_jobs(conn, start, end, source, area),
            "cancellation_rate": cancellation_rate(conn, start, end, source, area, min_sample),
            "pass_manual_handle_rate": pass_manual_handle_rate(conn, start, end, source, area, min_sample),
            "closing_completion_rate": closing_completion_rate(conn, start, end, source, area, min_sample),
        },
        "technicians": technicians_summary(conn, start, end, min_sample),
        "sources": {s: source_summary(conn, s, start, end, min_sample) for s in distinct_sources(conn)},
        "ai_quality": {
            **ai_quality_summary(conn, start, end, min_sample),
            "accuracy_by_source": ai_quality_accuracy_by_source(conn, start, end, min_sample),
        },
        "data_quality": {
            "real_data_volume_note": "KPI values reflect the real database only; a small sample size (few jobs, zero closings, zero reviewed AI episodes) is expected to show insufficient_data for many KPIs until real operational history accumulates.",
        },
    }
