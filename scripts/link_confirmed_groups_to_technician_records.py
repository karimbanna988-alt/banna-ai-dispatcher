"""Links owner-CONFIRMED WhatsApp group registry rows to the real
technicians row they already name, where -- and only where -- the link is
deterministically provable from data the owner already confirmed.

The gap this closes: a registry row can be confirmed_by_owner=1 and carry
technician_name_unconfirmed='Ashraf' while technician_id stays NULL. In
that state _resolve_registry_technician() falls back to the free-text
name, which can never match an open job (see its docstring), so every
message in that group correlates as UNMATCHED forever -- even though the
technician exists in the database and the owner already confirmed whose
group it is.

DETERMINISTIC BAR (all three must hold -- no name-similarity guessing):
  1. the registry row is already confirmed_by_owner=1;
  2. its technician_name_unconfirmed matches a technicians.name exactly
     (case-insensitively, whitespace-trimmed), and exactly one such
     technician exists;
  3. that same technician's OWN dispatch_group/closing_group already
     points back at this group's exact_name -- i.e. the two records
     already agree, and this script only writes down the link that both
     sides independently assert.

Rule 3 is what makes this mechanical rather than a business decision.
Deliberately EXCLUDED by it: COMPANY_JOB_INTAKE / COMPANY_CLOSINGS rows
that name a technician (e.g. "Augusta - Adam" naming Sebai). A company
intake group is not owned by a technician -- that name records a routing
fact, not group ownership -- and no technician record points at it, so
rule 3 correctly rejects it. Those stay for the owner to decide.

Never changes a role, never changes confirmed_by_owner, never touches a
technician's own record, never touches jobs/events.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dispatcher import storage, db_safety  # noqa: E402


def find_deterministic_links(conn) -> list:
    """Pure query -- returns the rows that satisfy all three bar
    conditions. Writes nothing, so it is safe to call for a dry run."""
    destinations = {}
    for tech in storage.list_technicians(conn):
        for field in ("dispatch_group", "closing_group"):
            group_name = tech.get(field)
            if group_name:
                destinations.setdefault(group_name, []).append((tech["id"], tech["name"], field))

    names_seen = {}
    for tech in storage.list_technicians(conn):
        names_seen.setdefault(tech["name"].strip().lower(), []).append(tech["id"])

    links = []
    for row in storage.list_group_registry(conn):
        if not row.get("confirmed_by_owner") or row.get("technician_id"):
            continue
        claimed = (row.get("technician_name_unconfirmed") or "").strip().lower()
        if not claimed:
            continue
        # Rule 2: exactly one technician with that exact name.
        if len(names_seen.get(claimed, [])) != 1:
            continue
        # Rule 3: that technician's own record points back at this group.
        pointing = destinations.get(row["exact_name"], [])
        if len(pointing) != 1:
            continue
        tech_id, tech_name, via = pointing[0]
        if tech_name.strip().lower() != claimed:
            continue
        links.append({
            "group_id": row["group_id"], "exact_name": row["exact_name"], "role": row["role"],
            "technician_id": tech_id, "technician_name": tech_name, "proved_via": f"technicians.{via}",
        })
    return links


def run(conn) -> dict:
    links = find_deterministic_links(conn)
    for link in links:
        row = storage.get_group_registry_entry(conn, link["group_id"])
        storage.upsert_group_registry_entry(conn, {
            **row,
            "technician_id": link["technician_id"],
            # role, confirmed_by_owner, service_area, company and notes are
            # all carried through unchanged -- this only fills in the FK.
        })
    return {"linked": links}


if __name__ == "__main__":
    db_path = db_safety.resolve_db_path_from_env() or storage.DB_PATH
    conn = storage.connect(db_path)
    result = run(conn)
    print(f"Deterministically linked {len(result['linked'])} confirmed group(s) to their technician record:")
    for link in result["linked"]:
        print(f"  {link['exact_name']!r} [{link['role']}] -> {link['technician_name']} "
              f"(id={link['technician_id']}), proved via {link['proved_via']}")
    if not result["linked"]:
        print("  (none -- nothing met the deterministic bar)")
