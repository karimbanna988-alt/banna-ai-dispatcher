"""Real-data read model for LUXOR OPERATIONS DESK (luxor_desk.py).

Reuses the existing backend exactly -- dispatcher.storage,
dispatcher.policy, dispatcher.pipeline (via luxor_ops_shared's cached
wrappers), and luxor_ops_shared's already-tested query helpers
(fetch_jobs, list_technicians, fetch_all_events, get_latest_closing_details).
No second database, no second parser, no second decision engine, no
second persistence layer. Pure functions only where practical; the
handful that must build a Streamlit-cache-friendly connection take one
in as an argument, exactly like luxor_ops_shared's own functions.

This module also owns the live-queue canonicalization logic that used
to be inlined only in luxor_console_pages/dispatch_queue.py, so that
page and luxor_desk now share one tested implementation instead of two
independent forks of the same rules.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import luxor_ops_shared as shared
from dispatcher import storage
from dispatcher.policy import LUCKY_CLOSINGS_STREAM_KEY, get_policy
from dispatcher.rules.market_aliases import resolve_market_and_area
from dispatcher.rules.sla_config import format_age, urgency_level
from dispatcher.rules.service_areas import find_service_area
from dispatcher.rules import coverage_areas
from dispatcher.storage import MessageType

QUEUE_BUCKETS = ("Urgent", "New Jobs", "Corrections & Cancellations", "Status Requests", "Closings")
_CORRECTION_CANCEL_TYPES = {MessageType.CORRECTION.value, MessageType.CANCELLATION.value}
_STATUS_TYPES = {MessageType.STATUS.value, MessageType.UPDATE_REQUEST.value, MessageType.DUPLICATE.value, MessageType.ASSIGNMENT.value}
UPDATES_CLOSINGS_TYPES = _CORRECTION_CANCEL_TYPES | _STATUS_TYPES | {MessageType.CLOSING.value}


def parse_ts(raw: Optional[str]):
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_live(item: dict, boundary_dt) -> bool:
    """Fails open to "live" for a message with no parseable timestamp --
    a genuinely urgent job must never be silently hidden as backlog just
    because its timestamp didn't parse."""
    ts = parse_ts(item["received_at"] or item["fetched_at"])
    if ts is None or boundary_dt is None:
        return True
    return ts >= boundary_dt


def policy_stream_key(group_label: str, source_value: str) -> str:
    return LUCKY_CLOSINGS_STREAM_KEY if group_label == "CLOSINGS" else source_value


def queue_bucket(message_type: str, urgency: str) -> str:
    if urgency == "urgent":
        return "Urgent"
    if message_type == MessageType.NEW.value:
        return "New Jobs"
    if message_type in _CORRECTION_CANCEL_TYPES or message_type == MessageType.UNRECOGNIZED.value or message_type == MessageType.CLOSING_WITHOUT_DISPATCH.value:
        return "Corrections & Cancellations"
    if message_type in _STATUS_TYPES:
        return "Status Requests"
    if message_type == MessageType.CLOSING.value:
        return "Closings"
    return "Corrections & Cancellations"


def _content_fingerprint(raw_text: str) -> str:
    """Whitespace/case-normalized hash -- two messages with trivially
    different formatting but the same real content still fingerprint the
    same; two messages that actually say different things never do."""
    normalized = " ".join((raw_text or "").split()).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def canonicalize(entries: list) -> list:
    """One queue row per canonical *lifecycle event* -- (source, ticket,
    message_type, content fingerprint) -- not one row per (source,
    ticket) alone. A ticket's original new-job message, a later
    correction, a cancellation, a status request, and a closing are
    DIFFERENT lifecycle events for the same ticket and must each remain
    individually visible and actionable; only truly repeated raw copies
    of the exact same event (identical type + normalized content --
    reminders, re-sends) fold together into one row with a "+N related
    messages" count. A message with no usable ticket is never grouped
    with anything else (no text-similarity guessing across tickets); it
    always stands alone. The earliest-received member of a group is
    canonical (so displayed age reflects the true original event);
    later members are folded in as a visible count, never deleted,
    never dropped from the database."""
    groups: dict = {}
    order = []
    for entry in entries:
        item, job, classify_result, *_ = entry
        ticket = job.ticket_number.value
        if ticket:
            key = (
                job.source.value, ticket, classify_result.message_type.value,
                _content_fingerprint(item["raw_text"]),
            )
        else:
            key = (job.source.value, f"__no_ticket_{item['message_id']}")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(entry)

    canonical = []
    for key in order:
        members = groups[key]
        members.sort(key=lambda e: e[0]["received_at"] or e[0]["fetched_at"] or "")
        canonical.append((members[0], len(members) - 1))
    return canonical


@dataclass
class QueueEntry:
    item: dict
    job: object
    classify_result: object
    result: object
    tracking: dict
    urgency: str
    age_seconds: float
    extra_count: int


def get_operational_counts(conn: sqlite3.Connection) -> dict:
    """Every count shown anywhere in the app must trace back to this one
    computation -- Dispatch's filter badges, header metrics, etc. all
    call this rather than recomputing their own numbers."""
    live, backlog, other = get_live_dispatch_queue(conn)
    urgent = sum(1 for e in live if e.urgency == "urgent")
    new_jobs = sum(1 for e in live if queue_bucket(e.classify_result.message_type.value, e.urgency) == "New Jobs")
    active_jobs = shared.fetch_jobs(conn)
    active_count = sum(1 for j in active_jobs if j["status"] in shared.ACTIVE_STATUSES)
    closed = [j for j in active_jobs if j["status"] == "Closed"]
    unsettled = sum(
        1 for j in closed
        if (conn.execute("SELECT reconciliation_state FROM jobs WHERE id = ?", (j["id"],)).fetchone() or [None])[0] != "Settled"
    )
    return {
        "live": len(live), "backlog": len(backlog), "other": len(other),
        "urgent": urgent, "new_jobs": new_jobs,
        "active_jobs": active_count, "pending_closings": unsettled,
    }


def get_live_dispatch_queue(conn: sqlite3.Connection) -> tuple[list[QueueEntry], list[dict], list[dict]]:
    """Returns (live_canonical_entries, backlog_pending, other_messages).
    live_canonical_entries is deduplicated by (source, ticket,
    message_type) -- see canonicalize()'s own docstring for why message
    type is part of the identity -- and sorted oldest-first within
    urgency (urgent first). Every entry traces to a real
    quo_pending_messages row -- nothing here is invented.

    Read-only: this function computes dispatch_tracking.get_dispatch_tracking()
    reads and the pipeline preview, but never calls mark_first_seen()/
    mark_luxor_prepared() -- those are stamped at the actual ingestion
    boundary (dispatcher.quo_ingest.poll_for_new_messages), not here.
    Merely opening or refreshing the Dispatch page must never change a
    message's recorded lifecycle -- a read model reads and calculates
    display data; it does not record operational facts."""
    live_queue_started_at = storage.ensure_live_queue_activated(conn)
    boundary_dt = parse_ts(live_queue_started_at)

    pending = shared.fetch_pending_messages(conn)
    live_pending = [item for item in pending if is_live(item, boundary_dt)]
    backlog_pending = [item for item in pending if not is_live(item, boundary_dt)]

    other_messages = []
    live_actionable = []
    for item in live_pending:
        job, classify_result = shared.cached_extract_and_classify(item["raw_text"], conn)
        if not shared.shows_job_transformation(classify_result, job):
            other_messages.append(item)
            continue

        tracking = storage.get_dispatch_tracking(conn, item["message_id"])
        acknowledged = tracking["acknowledged_at"] is not None
        dispatched = tracking["dispatched_manually_at"] is not None
        if dispatched:
            continue

        result = shared.cached_full_preview(item["raw_text"], conn)

        received = item["received_at"] or item["fetched_at"] or tracking["first_seen_at"]
        dt = parse_ts(received)
        age_seconds = (datetime.now(timezone.utc) - dt).total_seconds() if dt else 0
        urgency = urgency_level(age_seconds, acknowledged, dispatched)
        live_actionable.append((item, job, classify_result, result, tracking, urgency, age_seconds))

    canonical = canonicalize(live_actionable)
    entries = [
        QueueEntry(item=e[0], job=e[1], classify_result=e[2], result=e[3], tracking=e[4], urgency=e[5], age_seconds=e[6], extra_count=extra)
        for e, extra in canonical
    ]
    urgency_rank = {"urgent": 0, "warning": 1, "attention": 2}
    entries.sort(key=lambda e: (urgency_rank.get(e.urgency, 3), e.item["received_at"] or e.item["fetched_at"] or ""))
    return entries, backlog_pending, other_messages


def get_active_jobs(conn: sqlite3.Connection) -> list[dict]:
    """Real committed jobs currently active -- same query
    luxor_console_pages/active_jobs.py already uses."""
    jobs = shared.fetch_jobs(conn)
    return [j for j in jobs if j["status"] in shared.ACTIVE_STATUSES]


def get_latest_technician_update(conn: sqlite3.Connection, job_id: int) -> Optional[dict]:
    """Most recent recorded technician update (a STATUS event carrying a
    dispatcher-selected update_type) for a job -- None if none has ever
    been recorded. Never invents a status; a job with nothing recorded
    yet must show that plainly, not a guessed default."""
    row = conn.execute(
        "SELECT update_type, eta_minutes, raw_message, created_at FROM events "
        "WHERE job_id = ? AND message_type = ? AND update_type IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (job_id, MessageType.STATUS.value),
    ).fetchone()
    if row is None:
        return None
    columns = ("update_type", "eta_minutes", "raw_message", "created_at")
    return dict(zip(columns, row))


ACTIVE_JOB_WORKSPACE_STATUSES = shared.ACTIVE_STATUSES | {"Needs Review"}


def get_active_jobs_detailed(conn: sqlite3.Connection) -> list[dict]:
    """Everything the Active Jobs workspace needs: each active job plus
    its latest technician update. Deliberately broader than
    get_active_jobs()/shared.ACTIVE_STATUSES -- a job that landed on
    "Needs Review" because a Custom/ambiguous technician update couldn't
    be safely interpreted must stay visible and actionable here, not
    silently vanish from the workspace the dispatcher needs to resolve
    it from."""
    jobs = shared.fetch_jobs(conn)
    jobs = [j for j in jobs if j["status"] in ACTIVE_JOB_WORKSPACE_STATUSES]
    for job in jobs:
        job["latest_technician_update"] = get_latest_technician_update(conn, job["id"])
    return jobs


def get_closing_candidates(conn: sqlite3.Connection) -> list[dict]:
    """Jobs ready for the Closings workspace: dispatched jobs still "In
    Progress" (ready to receive a technician's closing) plus jobs already
    "Closed" but not yet reconciliation_state == "Settled" (closing
    recorded, awaiting company-closing dispatch and COMPLETE JOB). A job
    can legitimately appear here and in Active Jobs at the same time --
    the two pages offer different, non-exclusive actions on the same
    underlying job."""
    jobs = shared.fetch_jobs(conn)
    result = []
    for j in jobs:
        if j["status"] == "In Progress":
            result.append(j)
        elif j["status"] == "Closed" and j.get("reconciliation_state") != "Settled":
            result.append(j)
    return result


def get_completed_jobs(conn: sqlite3.Connection) -> list[dict]:
    """Jobs with a real final status: Closed + reconciliation_state ==
    "Settled" (via complete_job()), or Cancelled/Rejected. This is the
    Completed / History page's query -- normal dispatch history belongs
    here, not on the generic Activity feed."""
    jobs = shared.fetch_jobs(conn)
    result = []
    for j in jobs:
        if j["status"] in ("Cancelled", "Rejected"):
            result.append(j)
        elif j["status"] == "Closed" and j.get("reconciliation_state") == "Settled":
            result.append(j)
    return result


def get_pending_closings(conn: sqlite3.Connection) -> list[dict]:
    """Real closed jobs awaiting reconciliation -- same query
    luxor_console_pages/closings.py already uses. Amount/payment method
    only ever come from storage.get_latest_closing_details(); a job with
    no structured closing recorded shows no amount, never a fabricated one."""
    jobs = shared.fetch_jobs(conn)
    closed = [j for j in jobs if j["status"] == "Closed"]
    unsettled = []
    for job in closed:
        row = conn.execute("SELECT reconciliation_state FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        if not row or row[0] != "Settled":
            unsettled.append(job)
    return unsettled


def get_technician_coverage(conn: sqlite3.Connection) -> list[dict]:
    """The real technician roster, unchanged -- storage.list_technicians()
    directly, no substitution, no fabricated names."""
    return storage.list_technicians(conn, active_only=False)


def get_recent_activity(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    return shared.fetch_all_events(conn, limit=limit)


def resolve_job_routing(conn: sqlite3.Connection, job, raw_text: str):
    """Canonical area + eligible technicians for one job -- identical
    resolution dispatch_queue.py already uses (address wins over market
    alias, Main Leads market label as fallback/cross-check only)."""
    service_areas = shared.get_service_areas()
    market_label = shared.extract_market_label(raw_text) if job.source.value == "Main Leads" else None
    area_lookup = find_service_area(job.city.value, job.state.value, service_areas)
    # An inactive area stays unroutable -- unchanged, and checked first
    # so the coverage map can never reopen somewhere deliberately closed.
    if area_lookup.status == "inactive":
        address_area = None
    else:
        # State-aware (city, state) -> coverage area. This runs BEFORE the
        # service-area label because the two answer different questions:
        # service_areas says "do we work here", the coverage map says
        # "which area is this". Wilmington DE matched the whole-state DE
        # entry and produced the label "DE" while every Delaware
        # technician is registered as "Delaware", so nobody was eligible
        # for a job three people could take.
        address_area = coverage_areas.resolve_coverage_area(
            job.city.value, job.state.value)
        if not address_area and area_lookup.status == "confirmed":
            address_area = area_lookup.record.city or area_lookup.record.state
    resolution = resolve_market_and_area(address_area, market_label)
    eligible = []
    if resolution.canonical_area:
        eligible = storage.list_eligible_technicians_for(conn, resolution.canonical_area, job.service_type.value)
    return resolution, eligible, market_label


class LuxorDeskQueueReader:
    """The concrete implementation of dispatcher.application.QueueReader
    for this app. Lives here, at the repo root, specifically so
    application.py never has to import this module (or any luxor_desk_*
    module) to serve DispatcherService.get_queue() -- this class
    depends on the read model; the read model does not depend on it.
    The UI layer (luxor_desk_pages/*.py) constructs one instance and
    passes it into DispatcherService(..., queue_reader=...)."""

    def read_queue(self, conn: sqlite3.Connection, kind: str) -> list:
        if kind == "new_jobs":
            live, _backlog, _other = get_live_dispatch_queue(conn)
            return live
        if kind == "active":
            return get_active_jobs_detailed(conn)
        if kind == "closings":
            return get_closing_candidates(conn)
        if kind == "completed":
            return get_completed_jobs(conn)
        raise ValueError(f"Unknown queue kind: {kind!r} -- must be one of new_jobs/active/closings/completed")
