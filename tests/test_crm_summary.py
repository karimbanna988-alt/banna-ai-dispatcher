"""The CRM summary: today's business, honestly counted.

Two properties carry the weight here.

TODAY MEANS TODAY. The operational day is America/New_York, the same
boundary kpi_read_model already uses. A three-week-old unresolved review
row is real work but it is not today's, and letting it into today's
counts is how a quiet morning reads as a crisis.

A NUMBER NOBODY CAN COMPUTE IS NOT ZERO. Closing amounts are not
recorded anywhere yet, so revenue comes back as None and the UI shows a
dash. $0 would read like a terrible day rather than an absent feature.
"""
from datetime import datetime, timedelta, timezone
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from dispatcher import crm_summary as crm

NY = "America/New_York"
# 2026-08-17 14:00 UTC == 10:00 EDT, comfortably mid-afternoon locally.
NOW = datetime(2026, 8, 17, 14, 0, tzinfo=timezone.utc)


def candidate(ticket="JO11-AAAA", company="Mike Jobs", when=None, status="WAITING FOR HUMAN DISPATCH",
              review=None, city="Augusta", state="GA", service="Car Key", tech="Sebai"):
    return {
        "message_id": "m-" + ticket, "ticket": ticket, "source": company,
        "event_at": (when or NOW).isoformat(), "received_at": (when or NOW).isoformat(),
        "status": status, "review_reason": review, "city": city, "state": state,
        "service": service, "recommended": tech,
    }


# ================================================== the day boundary ===
def test_the_operational_day_is_new_york_not_utc():
    start, end = crm.range_bounds("today", NOW, NY)
    assert start.isoformat() == "2026-08-17T04:00:00+00:00"   # midnight EDT
    assert end.isoformat() == "2026-08-18T04:00:00+00:00"
    assert (end - start) == timedelta(days=1)


def test_a_late_evening_job_belongs_to_that_local_day():
    """11pm EDT Sunday is Sunday's work, even though it is Monday in UTC."""
    late = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)     # 11:30pm EDT Aug 16
    start, end = crm.range_bounds("today", NOW, NY)
    assert not (start <= late < end), "that job belongs to the previous operational day"
    y_start, y_end = crm.range_bounds("yesterday", NOW, NY)
    assert y_start <= late < y_end


def test_old_work_is_excluded_from_today():
    """THE defect this replaces: 500-hour-old review rows counted as
    today's workload."""
    rows = [candidate("NEW1", when=NOW),
            candidate("OLD1", when=NOW - timedelta(days=3)),
            candidate("OLD2", when=NOW - timedelta(days=30))]
    out = crm.summarize(rows, NOW, "today", NY)
    assert out["jobs_received"] == 1
    assert [j["ticket"] for j in out["recent_jobs"]] == ["NEW1"]


def test_wider_ranges_include_more_history():
    rows = [candidate("T1", when=NOW), candidate("Y1", when=NOW - timedelta(days=1))]
    assert crm.summarize(rows, NOW, "today", NY)["jobs_received"] == 1
    assert crm.summarize(rows, NOW, "yesterday", NY)["jobs_received"] == 1
    assert crm.summarize(rows, NOW, "month", NY)["jobs_received"] == 2


def test_the_timezone_is_reported_so_the_number_can_be_audited():
    out = crm.summarize([], NOW, "today", NY)
    assert out["timezone"] == NY
    assert out["range_start"] and out["range_end"]


# ====================================================== revenue ========
def test_unknown_revenue_is_not_reported_as_zero():
    out = crm.summarize([candidate()], NOW, "today", NY)
    assert out["verified_revenue"] is None
    assert out["revenue_available"] is False


def test_revenue_is_never_invented_from_job_counts():
    many = [candidate("T%d" % i) for i in range(20)]
    out = crm.summarize(many, NOW, "today", NY)
    assert out["verified_revenue"] is None


# ======================================================= counting ======
def test_jobs_are_counted_once_per_company_and_ticket():
    rows = [candidate("SAME1"), candidate("SAME1")]
    assert crm.summarize(rows, NOW, "today", NY)["jobs_received"] == 1


def test_the_same_ticket_for_two_companies_is_two_jobs():
    """Mike Jobs and Lucky are competitors; a shared ticket string is a
    coincidence, not one job."""
    rows = [candidate("SHARE", company="Mike Jobs"), candidate("SHARE", company="Lucky")]
    out = crm.summarize(rows, NOW, "today", NY)
    assert out["jobs_received"] == 2
    assert {c["name"] for c in out["by_company"]} == {"Mike Jobs", "Lucky"}


def test_chatter_without_a_ticket_is_not_a_job():
    rows = [candidate("REAL1"), dict(candidate("X"), ticket=None)]
    assert crm.summarize(rows, NOW, "today", NY)["jobs_received"] == 1


@pytest.mark.parametrize("status,review,bucket", [
    ("WAITING FOR HUMAN DISPATCH", None, "open"),
    ("ALREADY DISPATCHED", None, "open"),
    ("CLOSING OBSERVED", None, "completed"),
    ("HUMAN_REVIEW_NEEDED", None, "needs_attention"),
    ("WAITING FOR HUMAN DISPATCH", "COMPANY UNRESOLVED", "needs_attention"),
])
def test_statuses_map_to_owner_buckets(status, review, bucket):
    out = crm.summarize([candidate(status=status, review=review)], NOW, "today", NY)
    assert out[bucket] == 1


def test_the_buckets_sum_to_the_job_count():
    rows = [candidate("A", status="WAITING FOR HUMAN DISPATCH"),
            candidate("B", status="ALREADY DISPATCHED"),
            candidate("C", status="CLOSING OBSERVED"),
            candidate("D", review="COMPANY UNRESOLVED")]
    out = crm.summarize(rows, NOW, "today", NY)
    assert out["open"] + out["completed"] + out["needs_attention"] == out["jobs_received"] == 4


# ==================================================== breakdowns =======
def test_company_breakdown_is_ranked_and_totals_correctly():
    rows = ([candidate("M%d" % i, company="Mike Jobs") for i in range(3)]
            + [candidate("L%d" % i, company="Lucky") for i in range(2)]
            + [candidate("A1", company="AGN")])
    out = crm.summarize(rows, NOW, "today", NY)
    assert [(c["name"], c["count"]) for c in out["by_company"]] == [
        ("Mike Jobs", 3), ("Lucky", 2), ("AGN", 1)]
    assert sum(c["count"] for c in out["by_company"]) == out["jobs_received"]


def test_area_breakdown_totals_correctly():
    rows = [candidate("A1", city="Augusta", state="GA"),
            candidate("A2", city="Augusta", state="GA"),
            candidate("C1", city="Charlotte", state="NC")]
    out = crm.summarize(rows, NOW, "today", NY)
    assert out["by_area"][0] == {"name": "Augusta, GA", "count": 2}
    assert sum(a["count"] for a in out["by_area"]) == 3


def test_missing_area_is_labelled_unknown_not_dropped():
    out = crm.summarize([candidate(city=None, state=None)], NOW, "today", NY)
    assert out["by_area"] == [{"name": "Unknown", "count": 1}]


def test_recent_jobs_are_newest_first_and_capped():
    rows = [candidate("T%02d" % i, when=NOW - timedelta(minutes=i)) for i in range(20)]
    out = crm.summarize(rows, NOW, "today", NY, recent_limit=5)
    assert len(out["recent_jobs"]) == 5
    assert out["recent_jobs"][0]["ticket"] == "T00"


def test_recent_rows_carry_only_business_fields():
    """No parser output, no correlation reasoning, no message text."""
    out = crm.summarize([candidate()], NOW, "today", NY)
    row = out["recent_jobs"][0]
    assert set(row) == {"message_id", "at", "company", "area", "service",
                        "technician", "ticket", "bucket"}
    for leaked in ("why", "detected", "proposed_message", "confidence", "review_reason"):
        assert leaked not in row


# ======================================================== purity =======
def test_the_summary_is_pure():
    import inspect
    src = inspect.getsource(crm)
    for forbidden in ("import sqlite3", "execute(", "requests", "urllib",
                      "datetime.now", "open("):
        assert forbidden not in src, f"summary must not {forbidden}"


def test_unusable_timestamps_never_crash_the_day():
    rows = [dict(candidate("BAD"), event_at="not a date", received_at=None),
            candidate("GOOD")]
    out = crm.summarize(rows, NOW, "today", NY)
    assert out["jobs_received"] == 1


def test_an_empty_day_reports_zeros_not_errors():
    out = crm.summarize([], NOW, "today", NY)
    assert out["jobs_received"] == 0 and out["by_company"] == [] and out["recent_jobs"] == []
    assert out["verified_revenue"] is None
