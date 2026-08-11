"""Adds Toto and Joe as real technicians.id rows -- both are real,
owner-confirmed technicians (see docs/WHATSAPP_ROUTING_MAP_REVIEW.md
Section 0 and the owner's own chat instructions) whose identity and
service area (Greenville) are directly stated facts, not inferred.
Only what's actually confirmed is stored:
  - name: confirmed (Toto / Joe)
  - areas: ["Greenville"] -- confirmed
  - dispatch_group/closing_group: the exact confirmed WhatsApp group
    names from the registry
  - skills: left empty -- the lockout/house-lock vs car-key/hard-job
    split is a ROUTING RULE between two technicians, not a skill tag on
    one technician, and inventing a skills_json entry for it isn't
    something the owner actually stated
  - vehicle_restrictions: left empty -- not stated

After creating each row, updates that technician's confirmed registry
entries (already role=TECH_DISPATCH/TECH_CLOSINGS, confirmed_by_owner=1)
to point technician_id at the new real id instead of only carrying
technician_name_unconfirmed -- this is what lets the deterministic job
matcher actually correlate their real traffic to jobs going forward.

Idempotent: does nothing if a technician with this exact name already
exists (case-insensitive) or if the registry row already has this
technician_id.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dispatcher import storage, db_safety  # noqa: E402

TECHNICIANS = [
    {
        "name": "Toto",
        "areas": ["Greenville"],
        "dispatch_group": "Toto Greenville Dispatch",
        "closing_group": "Toto Greenville Closings",
        "registry_group_ids": ["120363407762864599@g.us", "120363429123808793@g.us"],
    },
    {
        "name": "Joe",
        "areas": ["Greenville"],
        "dispatch_group": "Joe Greenville Dispatch/Saturday",
        "closing_group": "Joe Greenville Closings",
        "registry_group_ids": ["120363425260967940@g.us", "120363423478202519@g.us"],
    },
]


def run(conn) -> dict:
    result = {"created": [], "already_existed": [], "registry_updated": []}
    existing = {t["name"].strip().lower(): t for t in
                [storage.get_technician(conn, r[0]) for r in conn.execute("SELECT id FROM technicians").fetchall()]}

    for spec in TECHNICIANS:
        key = spec["name"].strip().lower()
        if key in existing:
            tech_id = existing[key]["id"]
            result["already_existed"].append({"name": spec["name"], "id": tech_id})
        else:
            tech_id = storage.add_technician(
                conn, name=spec["name"], areas=spec["areas"], skills=[], vehicle_restrictions=[],
                dispatch_group=spec["dispatch_group"], closing_group=spec["closing_group"],
            )
            result["created"].append({"name": spec["name"], "id": tech_id})

        for group_id in spec["registry_group_ids"]:
            row = storage.get_group_registry_entry(conn, group_id)
            if row is None:
                continue
            if row["technician_id"] == tech_id:
                continue
            storage.upsert_group_registry_entry(conn, {
                **row, "technician_id": tech_id,
            })
            result["registry_updated"].append({"group_id": group_id, "technician_id": tech_id})
    return result


if __name__ == "__main__":
    db_path = db_safety.resolve_db_path_from_env() or storage.DB_PATH
    conn = storage.connect(db_path)
    result = run(conn)
    print(f"Created: {result['created']}")
    print(f"Already existed: {result['already_existed']}")
    print(f"Registry rows updated with real technician_id: {result['registry_updated']}")
