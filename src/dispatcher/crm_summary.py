"""CRM summary: the business day, from data the system already holds.

Banna's daily value is not shaving seconds off a forward the dispatcher
already does in three taps. It is that every real job becomes structured,
searchable business history without anyone typing it in.

This module answers the questions an owner actually asks -- how many jobs
today, what state are they in, which companies sent them, which areas got
them -- and nothing else. It is a pure projection: no SQL, no IO, no
clock of its own. The caller passes already-fetched rows and the current
instant, which is what makes it deterministic and testable.

TWO RULES IT WILL NOT BEND:

  Today means today. The operational day is America/New_York (the same
  `operational_timezone` kpi_read_model already uses), not the browser's
  guess and not UTC. A job posted at 11pm Sunday belongs to Sunday, and a
  three-week-old review row is not today's workload.

  A number nobody can compute is not zero. Closing revenue is currently
  unrecorded -- `events.closing_amount` is NULL for every row -- so this
  returns None for it and the UI shows a dash. Printing $0 would be a
  lie that reads like a bad day.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

# The one operational day boundary, shared with the existing KPI model so
# two screens can never disagree about what "today" was.
OPERATIONAL_TIMEZONE = "America/New_York"

RANGE_TODAY = "today"
RANGE_YESTERDAY = "yesterday"
RANGE_WEEK = "week"
RANGE_MONTH = "month"
RANGES = (RANGE_TODAY, RANGE_YESTERDAY, RANGE_WEEK, RANGE_MONTH)

# Candidate statuses, grouped the way an owner thinks about them rather
# than the way the correlation engine names them.
_COMPLETED = ("CLOSING OBSERVED",)
_OPEN = ("ALREADY DISPATCHED", "WAITING FOR HUMAN DISPATCH")


def range_bounds(range_key: str, now_utc: datetime, tz_name: str = OPERATIONAL_TIMEZONE):
    """(start, end) in UTC for an operational range. End is exclusive."""
    tz = ZoneInfo(tz_name)
    local_now = now_utc.astimezone(tz)
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    if range_key == RANGE_YESTERDAY:
        start_local, end_local = midnight - timedelta(days=1), midnight
    elif range_key == RANGE_WEEK:
        start_local = midnight - timedelta(days=midnight.weekday())
        end_local = start_local + timedelta(days=7)
    elif range_key == RANGE_MONTH:
        start_local = midnight.replace(day=1)
        end_local = (start_local + timedelta(days=32)).replace(day=1)
    else:
        start_local, end_local = midnight, midnight + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _parse(value) -> Optional[datetime]:
    """ISO string or TEXT epoch -> aware UTC datetime. Never raises."""
    if value in (None, ""):
        return None
    text = str(value)
    if text.isdigit() and len(text) >= 9:
        try:
            return datetime.fromtimestamp(int(text), timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


def _bucket(candidate) -> str:
    """Which owner-facing bucket a candidate belongs to."""
    if candidate.get("review_reason") or candidate.get("needs_human_review"):
        return "needs_attention"
    status = candidate.get("status") or ""
    if status in _COMPLETED:
        return "completed"
    if status in _OPEN:
        return "open"
    if status == "HUMAN_REVIEW_NEEDED":
        return "needs_attention"
    return "open"


def summarize(candidates, now_utc: datetime, range_key: str = RANGE_TODAY,
              tz_name: str = OPERATIONAL_TIMEZONE, recent_limit: int = 12) -> dict:
    """The business day as an owner reads it.

    `candidates` are the rows the frozen correlation projection already
    produced -- this adds no interpretation of its own, it only counts
    and groups. Rows outside the range are excluded entirely: yesterday's
    unresolved review is real work, but it is not today's.
    """
    start, end = range_bounds(range_key, now_utc, tz_name)
    tz = ZoneInfo(tz_name)

    in_range, seen = [], set()
    for candidate in candidates or []:
        # Business time is the WhatsApp event time where it exists;
        # ingestion time is a fact about our pipeline, not about the job.
        stamp = _parse(candidate.get("event_at")) or _parse(candidate.get("received_at"))
        if stamp is None or not (start <= stamp < end):
            continue
        if not candidate.get("ticket"):
            continue          # chatter is not a job
        key = (candidate.get("source"), str(candidate.get("ticket")).strip().upper())
        if key in seen:
            continue          # one job, counted once
        seen.add(key)
        in_range.append((stamp, candidate))

    in_range.sort(key=lambda pair: pair[0], reverse=True)

    counts = {"open": 0, "completed": 0, "needs_attention": 0}
    by_company, by_area = {}, {}
    for _stamp, candidate in in_range:
        counts[_bucket(candidate)] += 1
        company = candidate.get("source") or "Unknown"
        by_company[company] = by_company.get(company, 0) + 1
        area = ", ".join(p for p in (candidate.get("city"), candidate.get("state")) if p) or "Unknown"
        by_area[area] = by_area.get(area, 0) + 1

    def ranked(mapping):
        return [{"name": name, "count": count}
                for name, count in sorted(mapping.items(), key=lambda kv: (-kv[1], kv[0]))]

    recent = []
    for stamp, candidate in in_range[:recent_limit]:
        recent.append({
            "message_id": candidate.get("message_id"),
            "at": stamp.isoformat(),
            "company": candidate.get("source") or "Unknown",
            "area": ", ".join(p for p in (candidate.get("city"), candidate.get("state")) if p),
            "service": candidate.get("service"),
            "technician": candidate.get("recommended") or candidate.get("actual_destination"),
            "ticket": candidate.get("ticket"),
            "bucket": _bucket(candidate),
        })

    return {
        "range": range_key,
        "timezone": tz_name,
        "range_start": start.isoformat(),
        "range_end": end.isoformat(),
        # "%-d" is not portable (it fails on Windows), so the leading
        # zero is trimmed explicitly.
        "range_label": start.astimezone(tz).strftime("%b ") + str(start.astimezone(tz).day),
        "jobs_received": len(in_range),
        "open": counts["open"],
        "completed": counts["completed"],
        "needs_attention": counts["needs_attention"],
        # Closing amounts are not recorded anywhere yet. None means "we do
        # not know", which the UI renders as a dash. It is never 0.
        "verified_revenue": None,
        "revenue_available": False,
        "by_company": ranked(by_company),
        "by_area": ranked(by_area),
        "recent_jobs": recent,
    }
