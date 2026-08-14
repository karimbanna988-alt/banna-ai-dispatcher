"""Owner demo console: shows what the AI dispatcher decides, and why.

Two modes:

  --dry-run    Replays a scripted conversation with no WhatsApp and no
               database at all. Use this to see the demo end-to-end
               before creating any chats.

  (default)    Watches the two registered demo WhatsApp groups live and
               explains every message as it arrives.

SAFETY
Read-only: opens the database with SQLite's mode=ro URI flag, so this
process cannot write even if something in it tried to. It sends nothing
-- it prints the message a dispatcher WOULD send, for a human to copy.
The reasoning engine it drives (dispatcher.demo_dispatcher) is pure and
cannot reach production jobs/events at all.

The two demo groups are matched by EXACT WhatsApp group ID, configured
in .runtime/demo_chats.json. No name matching, so a production group can
never be picked up by this console.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from dispatcher import demo_dispatcher as dd  # noqa: E402
from dispatcher.parser import load_vehicle_rules  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "dispatcher.db"
DEMO_CONFIG = REPO_ROOT / ".runtime" / "demo_chats.json"

BAR = "=" * 78


def load_demo_chats() -> dict:
    """{exact_group_id: ROLE}. Missing config is not an error -- it just
    means the owner has not created the demo chats yet."""
    try:
        raw = json.loads(DEMO_CONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    mapping = {}
    if raw.get("company_chat_id"):
        mapping[raw["company_chat_id"]] = dd.ROLE_COMPANY
    if raw.get("technician_chat_id"):
        mapping[raw["technician_chat_id"]] = dd.ROLE_TECHNICIAN
    return mapping


def show(decision, source_label: str, raw_text: str):
    print()
    print(BAR)
    print(f"  {source_label}")
    print(f"  message: {' '.join((raw_text or '').split())[:70]!r}")
    print(BAR)
    print(f"  DETECTED   : {decision.detected}")
    print(f"  DECISION   : {decision.decision}")
    for i, w in enumerate(decision.why):
        print(f"  {'WHY        :' if i == 0 else '             '} {w}")
    if decision.confidence:
        print(f"  CONFIDENCE : {decision.confidence}")
    print(f"  ACTION     : {decision.proposed_action}")
    if decision.missing_fields:
        print(f"  MISSING    : {', '.join(decision.missing_fields)}  (never invented)")
    if decision.state_before != decision.state_after:
        print(f"  STATE      : {decision.state_before} -> {decision.state_after}")
    else:
        print(f"  STATE      : {decision.state_after}")
    if decision.proposed_message:
        print("  PROPOSED MESSAGE (nothing is sent -- copy it yourself):")
        for line in decision.proposed_message.split("\n"):
            print(f"      | {line}")
    if decision.needs_human:
        print("  >>> HUMAN REVIEW NEEDED")


SCRIPTED = [
    (dd.ROLE_COMPANY,
     "Power By Ucallz App\n\nDemo Company\nTicket: DEMO-001\nName: Demo Customer\n"
     "Phone: (555) 010-0000\nAddress: 1 Demo Street, Demo City\nDesc: House lockout\n"),
    (dd.ROLE_TECHNICIAN, "K"),
    (dd.ROLE_TECHNICIAN, "on my way, 20 minutes"),
    (dd.ROLE_TECHNICIAN, "all done"),          # deliberately no amount
    (dd.ROLE_TECHNICIAN, "done 150 cash"),
]


def dry_run():
    vr = load_vehicle_rules()
    job = dd.DemoJob()
    print(BAR)
    print("  DEMO DRY RUN -- no WhatsApp, no database, nothing sent".center(78))
    print(BAR)
    for role, text in SCRIPTED:
        label = "CHAT A (demo company)" if role == dd.ROLE_COMPANY else "CHAT B (demo technician)"
        show(dd.reason(role, text, job, vr), label, text)
    print()
    print(BAR)
    print(f"  FINAL DEMO STATE: {job.state}   ticket={job.ticket}   "
          f"amount={job.closing_amount or 'none'} {job.closing_payment_method or ''}")
    print("  No real job was created, matched, or modified.".center(78))
    print(BAR)


def live():
    chats = load_demo_chats()
    if not chats:
        print(f"No demo chats configured yet ({DEMO_CONFIG}).")
        print("Create the two WhatsApp groups, then run:")
        print("  python scripts/register_demo_chats.py --list")
        return
    vr = load_vehicle_rules()
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    job = dd.DemoJob()
    last_id = conn.execute("SELECT COALESCE(MAX(id),0) FROM whatsapp_pending_observations").fetchone()[0]
    print(BAR)
    print("  AI DISPATCHER -- LIVE DEMO (read-only, sends nothing)".center(78))
    print(f"  watching {len(chats)} demo chat(s) by exact id; production groups ignored".center(78))
    print(BAR)
    while True:
        rows = conn.execute(
            "SELECT id, group_id, group_name, text FROM whatsapp_pending_observations "
            "WHERE id > ? ORDER BY id ASC", (last_id,)).fetchall()
        for r in rows:
            last_id = r["id"]
            role = chats.get(r["group_id"])
            if not role:
                continue  # a production group -- never interpreted by the demo
            label = ("CHAT A (demo company)" if role == dd.ROLE_COMPANY
                     else "CHAT B (demo technician)") + f" -- {r['group_name']}"
            show(dd.reason(role, r["text"], job, vr), label, r["text"])
        time.sleep(2)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="replay a scripted demo, no WhatsApp or DB")
    args = p.parse_args()
    dry_run() if args.dry_run else live()
