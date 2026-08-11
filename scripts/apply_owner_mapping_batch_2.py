"""Applies the owner's second mapping-confirmation batch. Every group_id
below is exactly as the owner confirmed against the compact decision
batch previously presented -- no name-based inference performed here.

1. Ziyadah Dispatch/Sunday -> TECH_DISPATCH, technician Ziyadah (existing
   id), area Raleigh (already Ziyadah's DB area -- owner confirmed the
   group, not a new area).
2. Vancouver Dispatch -> TECH_DISPATCH, technician Falcon (existing id,
   NOT a new "Vancouver" technician), area Vancouver.
3. Kimo Alabama Dispatch/Closings -> TECH_DISPATCH/TECH_CLOSINGS,
   technician Kimo (existing id -- explicitly NOT a second Kimo record),
   area Alabama. Owner-stated CURRENT reassignment from Greenville to
   Alabama: Kimo's technicians row is updated via the audited
   update_technician_areas()/update_technician_destinations() paths
   (never touches jobs/events -- historical rows stay historical truth).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dispatcher import storage, db_safety  # noqa: E402

ZIYADAH_GROUP = "120363421773052415@g.us"
VANCOUVER_GROUP = "120363424128482715@g.us"
KIMO_DISPATCH_GROUP = "120363411941241893@g.us"
KIMO_CLOSING_GROUP = "120363408595340686@g.us"


def _confirm(conn, group_id, *, role, technician_id, service_area):
    row = storage.get_group_registry_entry(conn, group_id)
    if row is None:
        raise SystemExit(f"No registry row for {group_id} -- run the seed script first.")
    storage.upsert_group_registry_entry(conn, {
        **row,
        "role": role,
        "technician_id": technician_id,
        "technician_name_unconfirmed": None,
        "service_area": service_area,
        "confirmed_by_owner": True,
        "source": "owner_manual",
        "notes": "Owner-confirmed via mapping decision batch 2.",
    })
    return storage.get_group_registry_entry(conn, group_id)


def _find_technician_id(conn, name: str) -> int:
    for row in conn.execute("SELECT id FROM technicians WHERE name = ?", (name,)):
        return row[0]
    raise SystemExit(f"No technician named {name!r} in the technicians table.")


def run(conn) -> dict:
    ziyadah_id = _find_technician_id(conn, "Ziyadah")
    falcon_id = _find_technician_id(conn, "Falcon")
    kimo_id = _find_technician_id(conn, "Kimo")

    results = {}
    results["ziyadah"] = _confirm(conn, ZIYADAH_GROUP, role="TECH_DISPATCH", technician_id=ziyadah_id, service_area="Raleigh")
    results["vancouver"] = _confirm(conn, VANCOUVER_GROUP, role="TECH_DISPATCH", technician_id=falcon_id, service_area="Vancouver")
    results["kimo_dispatch"] = _confirm(conn, KIMO_DISPATCH_GROUP, role="TECH_DISPATCH", technician_id=kimo_id, service_area="Alabama")
    results["kimo_closing"] = _confirm(conn, KIMO_CLOSING_GROUP, role="TECH_CLOSINGS", technician_id=kimo_id, service_area="Alabama")

    results["kimo_areas_update"] = storage.update_technician_areas(conn, kimo_id, ["Alabama"])
    results["kimo_destinations_update"] = storage.update_technician_destinations(
        conn, kimo_id, "Kimo Alabama Dispatch", "Kimo Alabama Closings",
    )
    return results


if __name__ == "__main__":
    db_path = db_safety.resolve_db_path_from_env() or storage.DB_PATH
    conn = storage.connect(db_path)
    result = run(conn)
    for key, value in result.items():
        print(f"{key}: {value}")
