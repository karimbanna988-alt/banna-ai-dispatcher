"""Forward-only Quo ingestion runner.

The poller itself is UNCHANGED: this calls the existing
``quo_ingest.poll_for_new_messages`` with the existing configured
dispatch groups. All this adds is a loop and a process to run it in,
because the only thing that ever drove the poller was the retired
Streamlit UI -- which is why ingestion has been stopped since August.

FORWARD-ONLY BY DEFAULT. The stored checkpoint is from 2026-08-01, so a
naive start would pull three months of history and present it as new
work. On first run this moves the checkpoint to "now" instead, so only
messages that arrive from this moment are ingested. The backlog is not
deleted and not lost -- it stays in Quo, and can be ingested later by
deliberately rewinding the checkpoint once there is a plan for how
historical messages are labelled so they never look like today's jobs.

Quo access is GET-only; nothing is ever sent. Writes go only to our own
quo_pending_messages / quo_processed_messages tables, which is what
ingestion means.

Usage:
    python scripts/run_quo_poller.py                 # forward-only, loops
    python scripts/run_quo_poller.py --once          # single poll
    python scripts/run_quo_poller.py --keep-checkpoint  # do NOT skip backlog
"""

import argparse
import pathlib
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv                    # noqa: E402

# Same .env the read-only probe uses. The key is never printed.
load_dotenv(ROOT / ".env")

from dispatcher import storage                      # noqa: E402
from dispatcher.quo_client import QuoClient          # noqa: E402
from dispatcher.quo_ingest import poll_for_new_messages  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="poll once and exit")
    parser.add_argument("--interval", type=int, default=60, help="seconds between polls")
    parser.add_argument("--keep-checkpoint", action="store_true",
                        help="do NOT skip the backlog (will ingest everything since the stored checkpoint)")
    args = parser.parse_args()

    conn = storage.connect()
    try:
        existing = storage.get_quo_checkpoint(conn)
        if not args.keep_checkpoint:
            now = datetime.now(timezone.utc).isoformat()
            if existing:
                age_days = (datetime.now(timezone.utc)
                            - datetime.fromisoformat(existing.replace("Z", "+00:00"))).days
                print(f"stored checkpoint {existing} is {age_days} days old")
            print(f"moving checkpoint forward to {now} (backlog skipped, not deleted)")
            storage.set_quo_checkpoint(conn, now)
        else:
            print(f"keeping stored checkpoint {existing} -- backlog WILL be ingested")
    finally:
        conn.close()

    client = QuoClient()
    while True:
        conn = storage.connect()
        try:
            outcome = poll_for_new_messages(conn, client)
            stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
            queued = getattr(outcome, "queued", None)
            count = len(queued) if queued is not None else getattr(outcome, "stored_count", 0)
            if count:
                print(f"[{stamp}] ingested {count} new Quo message(s)")
            else:
                print(f"[{stamp}] no new Quo messages")
        except Exception as exc:                     # keep the loop alive
            print(f"[poll error] {type(exc).__name__}: {exc}")
        finally:
            conn.close()
        if args.once:
            return
        time.sleep(max(15, args.interval))


if __name__ == "__main__":
    main()
