"""Registers the two owner-created demo WhatsApp groups, by EXACT id.

WHY GROUPS AND NOT 1:1 CHATS
The bridge structurally refuses non-group messages
(messageHandler.js: `if (!message.isGroupMessage) return`). Using two
groups the owner creates means the demo needs NO change to that
receive-only, groups-only boundary -- the safest available option.

WHAT THIS DOES
  --list                  ask the running bridge for the group list so
                          the owner can find the two demo group ids
  --company X --technician Y
                          write .runtime/demo_chats.json and add both
                          ids to the bridge allowlist so their messages
                          are captured at all

ISOLATION
The demo groups are deliberately NOT added to whatsapp_group_registry.
Production therefore sees them as unmapped and refuses to interpret them
(GROUP_MAPPING_REQUIRED) -- exactly the behaviour production applies to
any unknown group. Only scripts/demo_dispatcher_console.py interprets
them, matching on exact id. Nothing here can create or modify a job.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = REPO_ROOT / ".runtime" / "whatsapp-shadow-session" / "allowlist.json"
DEMO_CONFIG = REPO_ROOT / ".runtime" / "demo_chats.json"
BRIDGE_GROUPS_URL = "http://127.0.0.1:8514/groups"


def list_groups(filter_text: str = ""):
    try:
        with urllib.request.urlopen(BRIDGE_GROUPS_URL, timeout=120) as resp:
            groups = json.loads(resp.read().decode("utf-8")).get("groups", [])
    except Exception as exc:  # noqa: BLE001
        print(f"Could not reach the bridge at {BRIDGE_GROUPS_URL}: {exc}")
        print("Is the bridge running and paired?")
        return
    needle = filter_text.lower()
    shown = [g for g in groups if needle in (g.get("name") or "").lower()] if needle else groups
    print(f"{len(shown)} group(s)" + (f" matching {filter_text!r}" if needle else "") + ":")
    for g in shown:
        print(f"  {g['id']}   {g.get('name')}")
    if needle and not shown:
        print("  (nothing matched -- create the groups in WhatsApp first, then re-run)")


def _load_allowlist() -> dict:
    try:
        return json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"groups": []}


def register(company_id: str, technician_id: str, company_name: str, technician_name: str):
    if company_id == technician_id:
        raise SystemExit("The two demo chats must be different groups.")
    for gid in (company_id, technician_id):
        if not gid.endswith("@g.us"):
            raise SystemExit(f"{gid!r} is not a WhatsApp group id (expected ...@g.us). "
                             "1:1 chats are not supported -- create groups instead.")

    data = _load_allowlist()
    existing = {g["id"] for g in data.get("groups", [])}
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    added = []
    for gid, name in ((company_id, company_name), (technician_id, technician_name)):
        if gid not in existing:
            data.setdefault("groups", []).append({"id": gid, "name": name, "addedAt": now})
            added.append(gid)
    ALLOWLIST.parent.mkdir(parents=True, exist_ok=True)
    ALLOWLIST.write_text(json.dumps(data, indent=2), encoding="utf-8")

    DEMO_CONFIG.write_text(json.dumps({
        "company_chat_id": company_id,
        "company_chat_name": company_name,
        "technician_chat_id": technician_id,
        "technician_chat_name": technician_name,
        "registered_at": now,
        "note": "Demo only. Matched by exact id. Deliberately absent from "
                "whatsapp_group_registry so production never interprets these.",
    }, indent=2), encoding="utf-8")

    print("Demo chats registered.")
    print(f"  CHAT A (company)    {company_id}  {company_name}")
    print(f"  CHAT B (technician) {technician_id}  {technician_name}")
    print(f"  allowlist: {len(added)} newly added, {len(existing)} already present")
    print()
    print("The bridge reads the allowlist per message, so no restart is needed.")
    print("Now run:  python scripts/demo_dispatcher_console.py")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--list", action="store_true", help="list the account's groups from the bridge")
    p.add_argument("--filter", default="", help="only show groups whose name contains this text")
    p.add_argument("--company", help="exact group id for CHAT A (demo company/job source)")
    p.add_argument("--technician", help="exact group id for CHAT B (demo technician)")
    p.add_argument("--company-name", default="DEMO Company Chat")
    p.add_argument("--technician-name", default="DEMO Technician Chat")
    a = p.parse_args()
    if a.list:
        list_groups(a.filter)
    elif a.company and a.technician:
        register(a.company, a.technician, a.company_name, a.technician_name)
    else:
        p.print_help()
        sys.exit(1)
