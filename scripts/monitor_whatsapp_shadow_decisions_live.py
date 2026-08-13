"""Read-only live view of the shadow dispatcher's actual interpretation
decisions -- the terminal equivalent of the CRM's WhatsApp live feed.

Sibling of monitor_whatsapp_observations_live.py, which shows only that a
message ARRIVED. This one additionally shows what the system CONCLUDED
about it: role, mapping status, interpretation, correlated job,
confidence, and the recommended next action.

Deliberately calls operator_api._interpret_whatsapp_observation() -- the
exact same function the real CRM endpoint uses -- rather than
reimplementing the logic, so what prints here can never drift from what
the CRM actually shows.

SAFETY: opens the database with SQLite's own mode=ro URI flag, so this
process cannot write even if something in it tried to. Prints no phone
number, no address, and no full message text (a short truncated preview
only). Never sends anything and never mutates any job.
"""
import os
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

DB_PATH = REPO_ROOT / "data" / "dispatcher.db"

# Point operator_api at the real DB read-only before importing it, so the
# import can never touch production data even transiently.
os.environ.setdefault("OPERATOR_API_DB_PATH", str(DB_PATH))

import operator_api as api  # noqa: E402
from dispatcher import storage  # noqa: E402
from dispatcher.parser import load_vehicle_rules  # noqa: E402

PREVIEW_CHARS = 38


def _preview(text: str) -> str:
    """One short, single-line, truncated excerpt -- enough to recognize the
    message shape without exposing customer detail."""
    flat = " ".join((text or "").split())
    return (flat[:PREVIEW_CHARS] + "...") if len(flat) > PREVIEW_CHARS else flat


def main():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    vehicle_rules = load_vehicle_rules()

    last_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM whatsapp_pending_observations").fetchone()[0]
    print("=" * 118)
    print("LUXOR SHADOW DISPATCHER -- LIVE DECISION VIEW (read-only, never sends, never mutates a job)")
    print(f"watching for observations after id={last_id}   db={DB_PATH}")
    print("=" * 118)

    while True:
        try:
            new_ids = [r["id"] for r in conn.execute(
                "SELECT id FROM whatsapp_pending_observations WHERE id > ? ORDER BY id ASC", (last_id,)
            ).fetchall()]
            if new_ids:
                # list_whatsapp_pending_observations() is what supplies the
                # registry-enriched shape _interpret_whatsapp_observation()
                # expects; re-read it whenever something new lands.
                enriched = {o["message_id"]: o for o in storage.list_whatsapp_pending_observations(conn, include_resolved=False)}
                id_to_msgid = {
                    r["id"]: r["message_id"] for r in conn.execute(
                        "SELECT id, message_id FROM whatsapp_pending_observations WHERE id > ?", (last_id,)
                    ).fetchall()
                }
                all_jobs = storage.list_jobs(conn)
                for obs_id in new_ids:
                    last_id = obs_id
                    obs = enriched.get(id_to_msgid.get(obs_id))
                    if obs is None:
                        continue  # already resolved/attached by a human -- nothing pending to show
                    result = api._interpret_whatsapp_observation(conn, obs, all_jobs, vehicle_rules)
                    job_disp = f"job#{result['proposed_job_id']}" if result.get("proposed_job_id") else "-"
                    print()
                    print(f"[{obs['received_at']}]  {obs['group_name']}")
                    print(f"    role={result['role_label']:<20} mapping={result['mapping_status']:<13} dir={obs['direction']}")
                    print(f"    text: {_preview(obs['text'])!r}")
                    print(f"    -> {result['interpretation']}")
                    print(f"    -> match={job_disp:<10} confidence={result.get('confidence') or '-':<12} NEXT: {result['next_action']}")
                    if result.get("missing_fields"):
                        print(f"    -> missing (never invented): {', '.join(result['missing_fields'])}")
        except sqlite3.OperationalError as e:
            print(f"[monitor] transient read error, retrying: {e}")
        time.sleep(2)


if __name__ == "__main__":
    main()
