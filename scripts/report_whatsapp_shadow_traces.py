"""Read-only report: reconstructs end-to-end job traces from live
WhatsApp shadow observations and prints them, newest last.

Answers the operational question receive-only shadowing is actually for:
"did we observe a whole job move through the workflow -- company intake,
dispatcher forward, technician, closing, company closing -- and how much
of that chain can we actually prove?"

Correlation is by EXACT ticket only (see dispatcher.shadow_trace's
docstring). Timing is displayed but never used to establish a link.

SAFETY: opens the database with SQLite's own mode=ro URI flag, so this
process cannot write even if something in it tried to. Prints no phone
number, no address, and no message body -- only the ticket, the group,
the role, the direction, and the timestamp.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from dispatcher.parser import extract_job, load_vehicle_rules  # noqa: E402
from dispatcher.shadow_trace import (  # noqa: E402
    CONFIDENCE_EXACT, TraceHop, actionable_traces, build_traces,
)

DB_PATH = REPO_ROOT / "data" / "dispatcher.db"

INCOMING_SQL = """
SELECT o.group_id, o.group_name, o.text, o.whatsapp_timestamp,
       r.role, r.confirmed_by_owner
FROM whatsapp_pending_observations o
LEFT JOIN whatsapp_group_registry r ON r.group_id = o.group_id
"""

OUTGOING_SQL = """
SELECT a.group_id, a.group_name, a.text, a.whatsapp_timestamp,
       r.role, r.confirmed_by_owner
FROM whatsapp_outgoing_audit a
LEFT JOIN whatsapp_group_registry r ON r.group_id = a.group_id
"""


def collect_hops(conn, vehicle_rules) -> list:
    """Extracts a ticket from every observed message that has one. A
    message with no parseable ticket simply contributes no hop -- it is
    never assigned to a trace by proximity."""
    hops = []
    for sql, direction in ((INCOMING_SQL, "incoming"), (OUTGOING_SQL, "outgoing")):
        for row in conn.execute(sql):
            job = extract_job(row["text"] or "", vehicle_rules)
            if not job.ticket_number.value:
                continue
            hops.append(TraceHop(
                ticket=job.ticket_number.value,
                group_name=row["group_name"],
                group_id=row["group_id"],
                role=row["role"],
                direction=direction,
                timestamp=row["whatsapp_timestamp"],
                confirmed_group=bool(row["confirmed_by_owner"]),
            ))
    return hops


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true",
                        help="include single-group tickets (UNMATCHED), not just real chains")
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    hops = collect_hops(conn, load_vehicle_rules())
    traces = build_traces(hops)
    shown = traces if args.all else actionable_traces(traces)

    print("=" * 100)
    print("WHATSAPP SHADOW TRACES -- reconstructed read-only, correlated by exact ticket".center(100))
    print("=" * 100)
    print(f"tickets seen: {len(traces)}   multi-group chains: {len(actionable_traces(traces))}")
    complete = [t for t in traces if t.is_complete]
    print(f"complete four-stage chains: {len(complete)}")
    print()

    for trace in shown:
        print(f"  ticket {trace.ticket}   [{trace.confidence}]"
              f"{'   COMPLETE' if trace.is_complete else ''}")
        for h in trace.hops:
            mark = "OUT" if h.direction == "outgoing" else "IN "
            role = h.role or "UNMAPPED"
            flag = "" if h.confirmed_group else "  (group not owner-confirmed)"
            print(f"      {mark}  {h.timestamp}  {h.group_name[:34]:<34} [{role}]{flag}")
        if trace.stages_covered:
            print(f"      stages: {' -> '.join(trace.stages_covered)}")
        for note in trace.notes:
            print(f"      note: {note}")
        print()

    print("=" * 100)
    print("Derived read-only. No job was created, matched, or modified by this report.".center(100))
    print("=" * 100)


if __name__ == "__main__":
    main()
