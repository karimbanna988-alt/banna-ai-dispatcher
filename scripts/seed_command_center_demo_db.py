"""Seeds a throwaway, on-disk SQLite database with synthetic (clearly
fake) jobs, technicians, and a WhatsApp observation, for Command Center
UI screenshots/manual verification only. NEVER touches data/dispatcher.db
-- pass a path with --db-path, always somewhere under a temp directory.

Every write below goes through the same real route handlers /
DispatcherService methods the real app uses (operator_api.route_*,
service.record_technician_update, etc.) -- the same helpers
tests/test_operator_api.py already relies on -- so the seeded data
exercises the real deterministic pipeline, not hand-built rows that
could drift from real behavior. The one exception (a backdated
last_event_at on one job, to demonstrate the "Attention now" stale-job
case) is a direct, clearly-commented UPDATE, only ever run against this
throwaway database.

Usage:
    python scripts/seed_command_center_demo_db.py --db-path /tmp/demo.db
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))


def future_ts(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def agn_message(ticket: str, area: str = "Charlotte, NC") -> str:
    return f"Ref: AGN Locksmith Services\nPDL: {ticket}\nPh: 1-555-010-3000\nAddr: 100 Demo St, {area}\nDesc: Car Lockout\n"


def main_leads_message(ticket: str, area: str = "Savannah, GA 31401") -> str:
    return (
        f"Power By Ucallz App\n\nDemo Locksmith Pro\nTicket: {ticket}\n"
        f"Name: Demo Customer\nPhone: (555) 010-2000\nAddress: 200 Demo Ave, {area}\nDesc: House Lockout\n"
    )


def seed(db_path: Path) -> None:
    import operator_api as api
    import whatsapp_bridge_adapter
    from dispatcher import storage
    from dispatcher.app_models import TechnicianUpdateCommand
    from dispatcher.rules.service_areas import load_service_areas

    conn = storage.connect(db_path)
    conn.execute(
        "INSERT INTO console_activation_state (id, live_queue_started_at, updated_at) VALUES (1, ?, ?)",
        ("2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
    )
    conn.commit()

    tech_a = storage.add_technician(conn, name="Demo Ashraf", areas=["Charlotte"], skills=["car lockout"], dispatch_group="Demo Ashraf Dispatch", closing_group="Demo Ashraf Closings")
    tech_b = storage.add_technician(conn, name="Demo Sebai", areas=["Savannah"], skills=["house lockout"], dispatch_group="Demo Sebai Dispatch", closing_group="Demo Sebai Closings")
    tech_c = storage.add_technician(conn, name="Demo Marcus", areas=["Charlotte", "Savannah"], skills=["car lockout", "house lockout"], dispatch_group="Demo Marcus Dispatch", closing_group="Demo Marcus Closings")
    conn.close()
    service_areas = load_service_areas()

    def seed_and_dispatch(message_id, text, group_label="AGN"):
        storage.save_pending_and_mark_processed(
            storage.connect(db_path), message_id=message_id, conversation_id=f"c-{message_id}",
            group_label=group_label, raw_text=text, from_number="+15550109900", received_at=future_ts(2),
        )
        api.route_acknowledge(None, {"message_id": message_id}, {})
        api.route_prepare_dispatch(None, {"message_id": message_id}, {})
        return api.route_confirm_dispatch(None, {"message_id": message_id}, {})["job_id"]

    # Job 1: dispatched only -- "Assigned", no closing yet.
    job1 = seed_and_dispatch("demo-m1", agn_message("D100"))

    # Job 2: dispatched + technician "Going" -> In Progress ("Closing due").
    job2 = seed_and_dispatch("demo-m2", agn_message("D200"))
    conn = api.get_conn()
    try:
        service = api._service(conn)
        service.record_technician_update(TechnicianUpdateCommand(job_id=job2, update_type="Going", raw_update="On my way"))
    finally:
        conn.close()

    # Job 3: fully closed today, real numeric amount -> Completed + Verified closing.
    job3 = seed_and_dispatch("demo-m3", main_leads_message("D300"), group_label="MAINLEADS")
    conn = api.get_conn()
    try:
        service = api._service(conn)
        service.record_technician_update(TechnicianUpdateCommand(job_id=job3, update_type="Going", raw_update="On my way"))
    finally:
        conn.close()
    api.route_closing_record(None, {"id": str(job3)}, {"raw_closing": "Done, paid cash", "final_amount": "95", "payment_method": "Cash"})
    prepared = api.route_closings(None, {}, {})
    if any(e["id"] == job3 and e.get("supported") is not False for e in prepared["entries"]):
        api.route_closing_prepare(None, {"id": str(job3)}, {})
        api.route_closing_confirm_sent(None, {"id": str(job3)}, {})
        api.route_closing_complete(None, {"id": str(job3)}, {})

    # Job 4: dispatched, then its last_event_at backdated so it exceeds the
    # stale-job SLA target -- the only non-route write in this script, and
    # only ever against this throwaway database, to demonstrate "Attention
    # now" surfacing a real stale-active-job case.
    job4 = seed_and_dispatch("demo-m4", agn_message("D400"))
    stale_at = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat().replace("+00:00", "Z")
    conn = storage.connect(db_path)
    conn.execute("UPDATE jobs SET last_event_at = ?, updated_at = ? WHERE id = ?", (stale_at, stale_at, job4))
    conn.commit()
    conn.close()

    # Job 5: a fresh, un-dispatched New job -- shows in Jobs/Command Center's "New" bucket.
    storage.save_pending_and_mark_processed(
        storage.connect(db_path), message_id="demo-m5", conversation_id="c-demo-m5",
        group_label="AGN", raw_text=agn_message("D500"), from_number="+15550109900", received_at=future_ts(1),
    )

    # ---------------------------------------------------- POPULATED ACTIVE BOARD --
    # The Command Center's Active Jobs board only shows jobs with a real
    # assigned technician (operator_ui_v2_connected/js/app.js filters on
    # j.technician_name) -- dispatching alone (jobs 1-4 above) never sets
    # that. storage.assign_technician() is the one real entry point for it
    # (per its own docstring), so it's called directly here exactly like
    # storage.add_technician() already is above -- not a hand-built row.
    def assign(job_id: int, technician_id: int) -> None:
        conn = storage.connect(db_path)
        try:
            result = storage.assign_technician(conn, job_id, technician_id, service_areas)
            if result.outcome not in ("assigned", "reassigned"):
                raise RuntimeError(f"assign_technician did not assign job {job_id}: {result}")
        finally:
            conn.close()

    def technician_update(job_id: int, update_type: str, raw_update: str, eta_minutes=None) -> None:
        conn = api.get_conn()
        try:
            service = api._service(conn)
            service.record_technician_update(TechnicianUpdateCommand(
                job_id=job_id, update_type=update_type, raw_update=raw_update, eta_minutes=eta_minutes,
            ))
        finally:
            conn.close()

    # F100: recently dispatched + assigned, technician has not replied yet
    # -- "Waiting for technician" on the board, no attention-now exception
    # (well within the SLA target).
    jobF1 = seed_and_dispatch("demo-mf1", agn_message("F100"))
    assign(jobF1, tech_a)

    # F200: assigned + a recent technician ETA update -- "Assigned" badge,
    # status flips to real "In Progress" (the one real status every
    # non-Custom technician update derives to).
    jobF2 = seed_and_dispatch("demo-mf2", agn_message("F200"))
    assign(jobF2, tech_c)
    technician_update(jobF2, "ETA", "15 minutes out", eta_minutes=15)

    # F300: a second, independent In Progress job from a different source
    # and technician -- the real population in_progress_count() now
    # counts (see the Section 1 KPI correction).
    jobF3 = seed_and_dispatch("demo-mf3", main_leads_message("F300"), group_label="MAINLEADS")
    assign(jobF3, tech_b)
    technician_update(jobF3, "Going", "On my way")

    # F400: technician marked the job Completed but the dispatcher hasn't
    # recorded the closing yet -- "Record closing" -> "Closing needed"
    # badge, and counts toward closings_due_count() (real, not fabricated:
    # status is still In Progress, exactly matching that function's
    # documented definition).
    jobF4 = seed_and_dispatch("demo-mf4", agn_message("F400"))
    assign(jobF4, tech_a)
    technician_update(jobF4, "Completed", "Job done, customer signed off")

    # F500: assigned + dispatched, then backdated stale -- surfaces both
    # in Attention Now (stale_active_job) and on the Active board with a
    # visibly old "time since last update", from a third technician and
    # the second source.
    jobF5 = seed_and_dispatch("demo-mf5", main_leads_message("F500"))
    assign(jobF5, tech_c)
    stale_at_f5 = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat().replace("+00:00", "Z")
    conn = storage.connect(db_path)
    conn.execute("UPDATE jobs SET last_event_at = ?, updated_at = ? WHERE id = ?", (stale_at_f5, stale_at_f5, jobF5))
    conn.commit()
    conn.close()

    # One synthetic WhatsApp observation, in-memory only (this process),
    # for the Active screen / Attention now WhatsApp-item demonstration.
    whatsapp_bridge_adapter.add_observation({
        "event_type": "incoming_message",
        "message_id": "demo-wa-1",
        "group_id": "0000000000@g.us",
        "group_name": "Demo Ashraf Dispatch",
        "sender": "demo-tech",
        "timestamp": future_ts(0),
        "text": "job done, customer paid cash",
    })

    # 30 extra raw historical messages, direct inserts (never through the
    # route pipeline -- these deliberately never become jobs), purely to
    # give Archive / Inbox Review real pagination to demonstrate. Ages
    # spread from 2 to 60 hours so they're all well outside the 60-minute
    # Jobs-screen window.
    conn = storage.connect(db_path)
    now = datetime.now(timezone.utc)
    for i in range(30):
        received = (now - timedelta(hours=2 + i)).isoformat().replace("+00:00", "Z")
        conn.execute(
            "INSERT INTO quo_pending_messages (message_id, group_label, raw_text, from_number, received_at, fetched_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (f"demo-archive-{i}", "AGN" if i % 2 == 0 else "MAINLEADS",
             f"Historical demo message #{i} -- status update chatter, never actioned.",
             "+15550109900", received, received),
        )
    conn.commit()
    conn.close()

    print(
        f"Seeded demo DB at {db_path}: "
        f"jobs {job1}, {job2}, {job3}, {job4}, {jobF1}, {jobF2}, {jobF3}, {jobF4}, {jobF5}; "
        f"technicians {tech_a} (Ashraf), {tech_b} (Sebai), {tech_c} (Marcus); 30 archive messages.\n"
        f"Active-board fixtures: F100={jobF1} (assigned, awaiting technician), "
        f"F200={jobF2} (assigned, recent ETA update), F300={jobF3} (In Progress, Going update), "
        f"F400={jobF4} (In Progress, Completed update -- closing needed), "
        f"F500={jobF5} (assigned, backdated stale)."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    args = parser.parse_args()
    seed(Path(args.db_path))
