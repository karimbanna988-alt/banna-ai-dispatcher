"""Applies the owner's fourth mapping-confirmation batch.

Every group_id below is exactly as owner-confirmed. No name-based
inference, and nothing here was promoted from a heuristic guess.

1. Ashraf Charlotte Dispatch -> technician Ashraf, area Charlotte. His
   Closings group was already confirmed and his technician record
   already named this group; only the registry row lagged.

2. Osos nj Closings -> technician Osos, area NJ. Explicitly Osos and
   NOT Mohamed Osama -- the owner has now stated this twice. Osos's
   closing_group was empty, so it is backfilled here (same pattern as
   Abozeid/Fares/Ziyadah in batch 3).

3. Luxor Savannah dispatch -> SHARED SAVANNAH WORKSTREAM.
   Owner-confirmed as a group belonging to an AREA, not to a person.
   It was created for Luxor but different technicians have worked it
   during different coverage periods, including Omar Shata.

   So technician_id stays NULL on purpose. This is the architectural
   distinction the owner asked to preserve:

       GROUP          -> AREA / WORKSTREAM
       CURRENT ROUTING -> TECHNICIAN

   With no technician on the registry row, the existing interpretation
   path already resolves no technician and returns UNMATCHED ->
   HUMAN_REVIEW_NEEDED, which is the correct conservative answer. No new
   mechanism was invented to express this; the schema already allowed a
   confirmed group with no technician.

4. Vancouver Closings -> technician Falcon, area Vancouver.
   Falcon's own technician record is deliberately NOT rewritten: the
   owner set his destinations by hand through the authenticated UI
   earlier today, and that entry is theirs, not this script's to
   "correct".

5. Trio Dispatch -> role IGNORE. Internal dispatcher communication
   between the owner and other dispatchers; not an operational routing
   destination. IGNORE is an existing registry role, so nothing new was
   added to express it. Its observations remain stored for audit --
   this only removes it from routing, candidates and evidence.

Idempotent: re-running changes nothing, because
upsert_group_registry_entry keys on the real WhatsApp group_id.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dispatcher import storage, db_safety  # noqa: E402

SAVANNAH_SHARED_NOTE = (
    "Owner-confirmed SHARED SAVANNAH WORKSTREAM. This group maps to an AREA, not to a "
    "person: it was created for Luxor but different technicians have used it during "
    "different coverage periods, including Omar Shata. technician_id is deliberately "
    "NULL. Do NOT infer permanent technician ownership from historical traffic here, and "
    "do NOT let repeated coverage become a permanent assignment. The current technician "
    "must be resolved from current routing/coverage evidence; if it cannot be resolved, "
    "the correct answer is HUMAN_REVIEW_NEEDED."
)

TRIO_INTERNAL_NOTE = (
    "Owner-confirmed INTERNAL dispatcher communication (owner + other dispatchers). "
    "Not an operational routing destination. Excluded from job candidates, technician "
    "routing, closing routing, TO DISPATCH and Apprentice evidence. Observations are "
    "retained for audit/history."
)

# (group_id, exact_name, role, technician_name_or_None, service_area, notes)
CONFIRMATIONS = [
    ("120363428199635701@g.us", "Ashraf Charlotte Dispatch", "TECH_DISPATCH", "Ashraf", "Charlotte",
     "Owner-confirmed via mapping decision batch 4."),
    ("120363420633655712@g.us", "Osos nj Closings", "TECH_CLOSINGS", "Osos", "NJ",
     "Owner-confirmed via mapping decision batch 4. Osos, explicitly NOT Mohamed Osama."),
    ("120363408345627351@g.us", "Luxor Savannah dispatch", "TECH_DISPATCH", None, "Savannah",
     SAVANNAH_SHARED_NOTE),
    ("120363406073039318@g.us", "Vancouver Closings", "TECH_CLOSINGS", "Falcon", "Vancouver",
     "Owner-confirmed via mapping decision batch 4."),
    ("120363410595686038@g.us", "Trio Dispatch", "IGNORE", None, None,
     TRIO_INTERNAL_NOTE),
]

# Only technicians whose destination is genuinely missing. Ashraf and
# Falcon already have destinations set and are left alone.
DESTINATION_UPDATES = {
    "Osos": ("Osos NJ dispatch", "Osos nj Closings"),
}


def _find_technician_id(conn, name: str):
    for row in conn.execute("SELECT id FROM technicians WHERE name = ?", (name,)):
        return row[0]
    return None


def run(conn) -> dict:
    results = {"confirmed": [], "destinations_updated": [], "skipped": []}

    for group_id, exact_name, role, tech_name, area, note in CONFIRMATIONS:
        row = storage.get_group_registry_entry(conn, group_id)
        if row is None:
            raise SystemExit(f"No registry row for {group_id} ({exact_name}) -- refusing to invent one.")
        if row["exact_name"] != exact_name:
            raise SystemExit(
                f"Registry name mismatch for {group_id}: stored {row['exact_name']!r} != {exact_name!r}. "
                "Refusing to confirm a mapping whose group identity does not match.")

        technician_id = None
        if tech_name:
            technician_id = _find_technician_id(conn, tech_name)
            if technician_id is None:
                raise SystemExit(f"Expected technician {tech_name!r} to exist but it does not.")

        storage.upsert_group_registry_entry(conn, {
            **row,
            "role": role,
            "technician_id": technician_id,
            "technician_name_unconfirmed": None,
            "service_area": area,
            "confirmed_by_owner": True,
            "source": "owner_manual",
            "notes": note,
        })
        results["confirmed"].append(storage.get_group_registry_entry(conn, group_id))

    for name, (dispatch_group, closing_group) in DESTINATION_UPDATES.items():
        tid = _find_technician_id(conn, name)
        if tid is None:
            raise SystemExit(f"Expected technician {name!r} to exist but it does not.")
        results["destinations_updated"].append(
            storage.update_technician_destinations(conn, tid, dispatch_group, closing_group))

    return results


if __name__ == "__main__":
    db_path = db_safety.resolve_db_path_from_env() or storage.DB_PATH
    conn = storage.connect(db_path)
    result = run(conn)
    print(f"Groups confirmed: {len(result['confirmed'])}")
    for c in result["confirmed"]:
        print(f"  {c['group_id']}  {c['exact_name']!r}")
        print(f"      role={c['role']}  technician_id={c['technician_id']}  area={c['service_area']!r}")
    for d in result["destinations_updated"]:
        print(f"Destinations updated: {d['name']} dispatch={d['dispatch_group']!r} closing={d['closing_group']!r}")
