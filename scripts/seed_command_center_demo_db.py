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

    conn = storage.connect(db_path)
    conn.execute(
        "INSERT INTO console_activation_state (id, live_queue_started_at, updated_at) VALUES (1, ?, ?)",
        ("2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
    )
    conn.commit()

    tech_a = storage.add_technician(conn, name="Demo Ashraf", areas=["Charlotte"], skills=["car lockout"], dispatch_group="Demo Ashraf Dispatch", closing_group="Demo Ashraf Closings")
    tech_b = storage.add_technician(conn, name="Demo Sebai", areas=["Savannah"], skills=["house lockout"], dispatch_group="Demo Sebai Dispatch", closing_group="Demo Sebai Closings")
    conn.close()

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

    print(f"Seeded demo DB at {db_path}: jobs {job1}, {job2}, {job3}, {job4}; technicians {tech_a}, {tech_b}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    args = parser.parse_args()
    seed(Path(args.db_path))
