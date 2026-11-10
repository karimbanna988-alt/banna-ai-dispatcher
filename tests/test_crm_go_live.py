"""The go-live boundary hides, it never deletes.

The owner asked to "start from clean". The dangerous reading of that
is to close or discard the jobs that came before. These tests pin the
safe reading: older jobs stay real, stay open, stay reachable, and the
view that excludes them says how many it excluded.
"""

import sys
import pathlib
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from dispatcher import crm_summary  # noqa: E402

NOW = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
GO_LIVE = "2026-11-09T00:00:00-05:00"


def _job(ticket, event_at, company="Co"):
    return {"message_id": "m-" + ticket, "ticket": ticket, "event_at": event_at,
            "source": company, "status": "WAITING FOR HUMAN DISPATCH"}


OLD = _job("OLD1", "2026-08-16T19:59:46+00:00")
OLDER = _job("OLD2", "2026-08-15T22:45:35+00:00")
NEW = _job("NEW1", "2026-11-09T14:11:37+00:00")
ALL = [OLD, OLDER, NEW]


def test_jobs_before_go_live_are_excluded_from_the_day():
    s = crm_summary.summarize(ALL, NOW, range_key="today", go_live_at=GO_LIVE)
    assert s["jobs_received"] == 1
    assert s["recent_jobs"][0]["ticket"] == "NEW1"


def test_the_view_states_how_many_it_is_hiding():
    s = crm_summary.summarize(ALL, NOW, range_key="today", go_live_at=GO_LIVE)
    assert s["hidden_by_go_live"] == 2


def test_the_backlog_view_returns_exactly_the_hidden_jobs():
    s = crm_summary.summarize(ALL, NOW, range_key="backlog", go_live_at=GO_LIVE)
    assert s["jobs_received"] == 2
    assert {j["ticket"] for j in s["recent_jobs"]} == {"OLD1", "OLD2"}


def test_backlog_and_live_together_account_for_every_job():
    """No job may fall between the two views."""
    live = crm_summary.summarize(ALL, NOW, range_key="all", go_live_at=GO_LIVE)
    back = crm_summary.summarize(ALL, NOW, range_key="backlog", go_live_at=GO_LIVE)
    assert live["jobs_received"] + back["jobs_received"] == len(ALL)


def test_a_backlogged_job_keeps_its_real_state():
    """Old is not a synonym for closed."""
    back = crm_summary.summarize(ALL, NOW, range_key="backlog", go_live_at=GO_LIVE)
    assert back["open"] == 2
    assert back["completed"] == 0


def test_no_boundary_means_nothing_is_hidden():
    s = crm_summary.summarize(ALL, NOW, range_key="all", go_live_at=None)
    assert s["jobs_received"] == 3
    assert s["hidden_by_go_live"] == 0


def test_the_jobs_list_applies_the_same_boundary_as_the_summary():
    rows = crm_summary.job_rows(ALL, NOW, range_key="all", go_live_at=GO_LIVE)
    summary = crm_summary.summarize(ALL, NOW, range_key="all", go_live_at=GO_LIVE)
    assert rows["total"] == summary["jobs_received"]
    assert rows["hidden_by_go_live"] == summary["hidden_by_go_live"]


def test_the_jobs_list_backlog_view_lists_the_old_jobs():
    rows = crm_summary.job_rows(ALL, NOW, range_key="backlog", go_live_at=GO_LIVE)
    assert {j["ticket"] for j in rows["jobs"]} == {"OLD1", "OLD2"}
