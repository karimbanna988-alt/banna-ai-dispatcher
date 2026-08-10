"""Read-only live monitor: polls whatsapp_pending_observations (LEFT
JOIN whatsapp_group_registry for role/mapping context) every ~2 seconds
and prints only NEW rows since the last poll. Opens the real database
with SQLite's own mode=ro URI flag -- this process cannot write even if
something in it tried to. Never prints phone numbers, addresses,
secrets, or full message text (a short, already-masked-at-source
preview only, truncated)."""
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "dispatcher.db"

QUERY = """
SELECT o.id, o.received_at, o.group_name, o.direction, o.message_id,
       r.role, r.confirmed_by_owner, r.company, r.technician_name_unconfirmed
FROM whatsapp_pending_observations o
LEFT JOIN whatsapp_group_registry r ON r.group_id = o.group_id
WHERE o.id > ?
ORDER BY o.id ASC
"""


def main():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    last_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM whatsapp_pending_observations").fetchone()[0]
    print(f"[monitor] watching whatsapp_pending_observations (read-only), starting after id={last_id}")
    print(f"[monitor] db: {DB_PATH}")
    print("-" * 100)
    while True:
        try:
            rows = conn.execute(QUERY, (last_id,)).fetchall()
        except sqlite3.OperationalError as e:
            print(f"[monitor] transient read error, retrying: {e}")
            time.sleep(2)
            continue
        for row in rows:
            (obs_id, received_at, group_name, direction, message_id,
             role, confirmed, company, technician) = row
            last_id = obs_id
            mapping = "CONFIRMED" if confirmed else "HUMAN_REVIEW"
            role_disp = role or "UNMAPPED_WORK"
            who = " ".join(x for x in (company, technician) if x)
            print(
                f"{received_at}  {group_name!r:45}  role={role_disp:20} "
                f"mapping={mapping:12} dir={direction:9} {who or '':20} mid={message_id[:40]}"
            )
        time.sleep(2)


if __name__ == "__main__":
    main()
