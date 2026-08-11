"""Applies the owner's third mapping-confirmation batch. Every group_id
below is exactly as owner-confirmed -- no name-based inference.

1. Abozeid Arlington Dispatch/Closings -> existing technician Abozeid
   (id already in DB, areas=["Arlington"]) -- first confirmed WhatsApp
   destinations for him, so technicians.dispatch_group/closing_group are
   set too (same pattern as Kimo in batch 2).
2. Fares Virginia Beach Dispatch/Closings -> existing technician Fares,
   same treatment.
3. Tarek columbia dispatch/closings -> existing technician Tarek
   (permanent home area Savannah, UNCHANGED). This is owner-stated
   TEMPORARY coverage (Ismail off today) -- modeled using the registry's
   own existing per-group service_area/notes fields, which are already
   independent of technicians.areas_json. Deliberately does NOT call
   update_technician_destinations for Tarek: his own technician record
   continues to reflect his permanent Savannah destinations, not this
   temporary Columbia coverage. No new "temporary routing" mechanism
   invented -- the existing schema already separates "what a group is
   confirmed to route to" (registry) from "a technician's permanent
   profile" (technicians table).
4. Osos NJ dispatch -> a NEW technician "Osos" (explicitly NOT Mohamed
   Osama -- the owner was explicit about this), area NJ. The paired
   "Osos nj Closings" group already exists in the registry as
   UNMAPPED_WORK and is presented as the next candidate, NOT confirmed
   here (the owner asked for it to be flagged, not guessed).
5. Ziyadah closings -> existing technician Ziyadah (already confirmed
   for Ziyadah Dispatch/Sunday in batch 2). Also backfills her
   dispatch_group/closing_group destinations, which batch 2 left unset.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dispatcher import storage, db_safety  # noqa: E402

TAREK_COLUMBIA_NOTE = (
    "Owner-confirmed TEMPORARY coverage: Tarek is covering Columbia because "
    "Ismail is off today. Tarek's PERMANENT home area remains Savannah "
    "(technicians.areas_json unchanged) -- this registry row's service_area "
    "reflects only this group's current routing, not a permanent reassignment. "
    "Do not treat as Tarek's permanent area. Ismail remains Columbia's normal technician."
)

# (group_id, exact_name, role, technician_name, service_area, notes)
CONFIRMATIONS = [
    ("120363403321651687@g.us", "Abozeid Arlington Dispatch", "TECH_DISPATCH", "Abozeid", "Arlington", None),
    ("120363422531151840@g.us", "Abozeid Arlington Closings", "TECH_CLOSINGS", "Abozeid", "Arlington", None),
    ("120363406134197775@g.us", "Fares Virginia Beach Dispatch", "TECH_DISPATCH", "Fares", "Virginia Beach", None),
    ("120363408179176581@g.us", "Fares Virginia Beach Closings", "TECH_CLOSINGS", "Fares", "Virginia Beach", None),
    ("120363425255488739@g.us", "Tarek columbia dispatch", "TECH_DISPATCH", "Tarek", "Columbia", TAREK_COLUMBIA_NOTE),
    ("120363425277781798@g.us", "Tarek columbia closings", "TECH_CLOSINGS", "Tarek", "Columbia", TAREK_COLUMBIA_NOTE),
    ("120363403736659314@g.us", "Osos NJ dispatch", "TECH_DISPATCH", "Osos", "NJ", None),
    ("120363420900690444@g.us", "Ziyadah closings", "TECH_CLOSINGS", "Ziyadah", "Raleigh", None),
]

# Technicians whose dispatch/closing destinations should be backfilled
# from this batch's confirmations -- Tarek is deliberately excluded
# (his own record must keep reflecting his permanent Savannah setup,
# not this temporary Columbia coverage).
DESTINATION_UPDATES = {
    "Abozeid": ("Abozeid Arlington Dispatch", "Abozeid Arlington Closings"),
    "Fares": ("Fares Virginia Beach Dispatch", "Fares Virginia Beach Closings"),
    "Ziyadah": ("Ziyadah Dispatch/Sunday", "Ziyadah closings"),
}


def _find_technician_id(conn, name: str):
    for row in conn.execute("SELECT id FROM technicians WHERE name = ?", (name,)):
        return row[0]
    return None


def run(conn) -> dict:
    results = {"confirmed": [], "technicians_created": [], "destinations_updated": []}

    osos_id = _find_technician_id(conn, "Osos")
    if osos_id is None:
        osos_id = storage.add_technician(
            conn, name="Osos", areas=["NJ"], skills=[], vehicle_restrictions=[],
            dispatch_group="Osos NJ dispatch", closing_group=None,
        )
        results["technicians_created"].append({"name": "Osos", "id": osos_id})

    tech_ids = {
        "Abozeid": _find_technician_id(conn, "Abozeid"),
        "Fares": _find_technician_id(conn, "Fares"),
        "Tarek": _find_technician_id(conn, "Tarek"),
        "Osos": osos_id,
        "Ziyadah": _find_technician_id(conn, "Ziyadah"),
    }
    for name, tid in tech_ids.items():
        if tid is None:
            raise SystemExit(f"Expected technician {name!r} to already exist but it doesn't.")

    for group_id, exact_name, role, tech_name, area, note in CONFIRMATIONS:
        row = storage.get_group_registry_entry(conn, group_id)
        if row is None:
            raise SystemExit(f"No registry row for {group_id} ({exact_name}) -- run the seed script first.")
        storage.upsert_group_registry_entry(conn, {
            **row,
            "role": role,
            "technician_id": tech_ids[tech_name],
            "technician_name_unconfirmed": None,
            "service_area": area,
            "confirmed_by_owner": True,
            "source": "owner_manual",
            "notes": note or "Owner-confirmed via mapping decision batch 3.",
        })
        results["confirmed"].append(storage.get_group_registry_entry(conn, group_id))

    for name, (dispatch_group, closing_group) in DESTINATION_UPDATES.items():
        result = storage.update_technician_destinations(conn, tech_ids[name], dispatch_group, closing_group)
        results["destinations_updated"].append(result)

    return results


if __name__ == "__main__":
    db_path = db_safety.resolve_db_path_from_env() or storage.DB_PATH
    conn = storage.connect(db_path)
    result = run(conn)
    print(f"Technicians created: {result['technicians_created']}")
    print(f"Destinations updated: {[(d['name'], d['dispatch_group'], d['closing_group']) for d in result['destinations_updated']]}")
    print(f"Groups confirmed: {len(result['confirmed'])}")
    for c in result["confirmed"]:
        print(f"  {c['group_id']} {c['exact_name']!r} -> {c['role']} tech_id={c['technician_id']} area={c['service_area']!r}")
