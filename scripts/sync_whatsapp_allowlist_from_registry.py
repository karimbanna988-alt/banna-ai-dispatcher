"""Writes whatsapp_shadow_bridge's allowlist.json from the (already-
seeded) whatsapp_group_registry table -- the ONE place group forwarding
permission is decided from now on. The registry itself is built from
exact WhatsApp group IDs (owner-confirmed facts + the offline name
analysis, already reviewed), never inferred at runtime by the Node
bridge, which only ever checks "is this exact id in allowlist.json?".

Idempotent: preserves each existing entry's original addedAt timestamp,
only adds entries for is_active=1 registry rows, never removes an
existing allowlist entry that isn't in the registry (so the pilot's
original Sebai/Augusta entry survives even if it were somehow missing a
registry row -- this script only ever grows the allowlist, deletions are
a separate, explicit, human action).

Does not touch the WhatsApp session, does not restart the bridge, does
not send anything.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dispatcher import storage, db_safety  # noqa: E402

ALLOWLIST_FILE = (
    Path(__file__).resolve().parent.parent / ".runtime" / "whatsapp-shadow-session" / "allowlist.json"
)


def sync(conn, allowlist_file: Path = ALLOWLIST_FILE) -> dict:
    try:
        existing = json.loads(allowlist_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        existing = {"groups": []}
    existing_by_id = {g["id"]: g for g in existing.get("groups", [])}

    active_rows = storage.list_group_registry(conn, active_only=True)
    added = 0
    for row in active_rows:
        gid = row["group_id"]
        if gid in existing_by_id:
            continue
        existing_by_id[gid] = {
            "id": gid,
            "name": row["exact_name"],
            "addedAt": datetime.now(timezone.utc).isoformat(),
        }
        added += 1

    result = {"groups": list(existing_by_id.values())}
    allowlist_file.parent.mkdir(parents=True, exist_ok=True)
    allowlist_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return {"total": len(result["groups"]), "added": added}


if __name__ == "__main__":
    db_path = db_safety.resolve_db_path_from_env()
    conn = storage.connect(db_path)
    result = sync(conn)
    print(f"Allowlist synced: {result['total']} total groups ({result['added']} newly added) -> {ALLOWLIST_FILE}")
