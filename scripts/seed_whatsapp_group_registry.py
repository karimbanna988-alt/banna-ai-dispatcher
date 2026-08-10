"""Seeds whatsapp_group_registry from:
  1. Owner-confirmed facts (hardcoded below, sourced directly from the
     owner's chat instructions -- these are facts, not name-guesses).
  2. The remaining name-heuristically "work-shaped" groups from the
     offline 372-group inventory analysis (docs/WHATSAPP_ROUTING_MAP_REVIEW.md),
     seeded as UNMAPPED_WORK -- observed for human review, never auto-
     assigned a technician or company.

Idempotent (upsert by group_id) -- safe to re-run. Does NOT touch the
WhatsApp session, does NOT modify the Node bridge's allowlist.json
(a separate step -- see scripts/sync_whatsapp_allowlist_from_registry.py),
does NOT send anything.

Only the two buckets below are inserted, deliberately excluding the
~83 UNKNOWN and ~206 NON_WORK/IGNORE groups from the offline analysis --
those remain completely unobserved.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dispatcher import storage, db_safety  # noqa: E402


# ============================================================ CONFIRMED FACTS
# Every entry here is confirmed_by_owner=True, is_active=True, source='owner_manual'.
CONFIRMED = [
    {"group_id": "120363424227833077@g.us", "exact_name": "Sebai Augusta Dispatch",
     "role": "TECH_DISPATCH", "service_area": "Augusta", "technician_name_unconfirmed": "Sebai",
     "notes": "Owner-confirmed. Real technicians.dispatch_group value matches exactly."},
    {"group_id": "120363426584048614@g.us", "exact_name": "Sebai augusta closing",
     "role": "TECH_CLOSINGS", "service_area": "Augusta", "technician_name_unconfirmed": "Sebai",
     "notes": "Owner-confirmed. Real technicians.closing_group value matches exactly."},
    {"group_id": "120363424464824773@g.us", "exact_name": "Adam - Greenville",
     "role": "COMPANY_JOB_INTAKE", "company": "Mike Jobs", "service_area": "Greenville",
     "notes": "Owner-confirmed. Technician depends on service type -- see company_routing_rules proposal (not yet built)."},
    {"group_id": "120363409883999573@g.us", "exact_name": "Adam - Greenville Closings",
     "role": "COMPANY_CLOSINGS", "company": "Mike Jobs", "service_area": "Greenville",
     "notes": "Owner-confirmed."},
    {"group_id": "120363400431562993@g.us", "exact_name": "Augusta - Adam",
     "role": "COMPANY_JOB_INTAKE", "company": "Mike Jobs", "service_area": "Augusta",
     "technician_name_unconfirmed": "Sebai", "notes": "Owner-confirmed: Mike Jobs + Augusta -> Sebai."},
    {"group_id": "120363399822651490@g.us", "exact_name": "Augusta - Adam - clossings",
     "role": "COMPANY_CLOSINGS", "company": "Mike Jobs", "service_area": "Augusta",
     "technician_name_unconfirmed": "Sebai", "notes": "Owner-confirmed."},
    {"group_id": "120363407762864599@g.us", "exact_name": "Toto Greenville Dispatch",
     "role": "TECH_DISPATCH", "company": "Mike Jobs", "service_area": "Greenville",
     "technician_name_unconfirmed": "Toto",
     "notes": "Owner-confirmed: Mike Jobs + Greenville + lockout/house-lock -> Toto. Not yet a technicians.id row."},
    {"group_id": "120363429123808793@g.us", "exact_name": "Toto Greenville Closings",
     "role": "TECH_CLOSINGS", "company": "Mike Jobs", "service_area": "Greenville",
     "technician_name_unconfirmed": "Toto", "notes": "Owner-confirmed."},
    {"group_id": "120363425260967940@g.us", "exact_name": "Joe Greenville Dispatch/Saturday",
     "role": "TECH_DISPATCH", "company": "Mike Jobs", "service_area": "Greenville",
     "technician_name_unconfirmed": "Joe",
     "notes": "Owner-confirmed: Mike Jobs + Greenville + car-key/hard-job -> Joe. Not yet a technicians.id row."},
    {"group_id": "120363423478202519@g.us", "exact_name": "Joe Greenville Closings",
     "role": "TECH_CLOSINGS", "company": "Mike Jobs", "service_area": "Greenville",
     "technician_name_unconfirmed": "Joe", "notes": "Owner-confirmed."},
    {"group_id": "120363429428302880@g.us", "exact_name": "GMI Adams job receiving",
     "role": "COMPANY_JOB_INTAKE", "company": "GMI", "notes": "Owner-confirmed: GMI is company/source."},
    {"group_id": "120363427915008647@g.us", "exact_name": "GMI Adams Savannah Jobs",
     "role": "COMPANY_JOB_INTAKE", "company": "GMI", "service_area": "Savannah", "notes": "Owner-confirmed."},
    {"group_id": "120363412686198496@g.us", "exact_name": "Adams closings GMI All Areas",
     "role": "COMPANY_CLOSINGS", "company": "GMI", "notes": "Owner-confirmed."},
    # DB-confirmed (not name-guessed): technicians.closing_group already
    # configured by the owner and matches these live group names exactly.
    {"group_id": "120363428143815156@g.us", "exact_name": "Ashraf Charlotte Closings",
     "role": "TECH_CLOSINGS", "service_area": "Charlotte", "technician_name_unconfirmed": "Ashraf",
     "notes": "Confirmed via exact match to technicians.closing_group (owner-entered DB config, not a name guess)."},
    {"group_id": "120363392480509639@g.us", "exact_name": "Ismail Columbia Closings",
     "role": "TECH_CLOSINGS", "service_area": "Columbia", "technician_name_unconfirmed": "Kabaka",
     "notes": "Confirmed via exact match to technicians.closing_group for Kabaka (owner-entered DB config). "
              "Group text says 'Ismail' -- DB says the technician is Kabaka; flagged for owner awareness."},
]

# ==================================================== UNMAPPED_WORK (offline-heuristic, unconfirmed)
# The remaining name-heuristically work-shaped groups from the offline
# analysis, minus everything already listed as CONFIRMED above. Each
# keeps its offline-guessed company/area as a NOTE only -- role is always
# UNMAPPED_WORK, never the heuristic bucket, so nothing here can be
# mistaken for a real classification at runtime.
UNMAPPED_WORK_SOURCE = Path(__file__).resolve().parent.parent / "docs" / "WHATSAPP_ROUTING_MAP_REVIEW.md"


def _load_unmapped_candidates(confirmed_ids: set) -> list:
    """Parses the Section 7 appendix table in the routing-map review doc
    (the corrected 6-bucket categorization, with the "Jobs Update N"
    false-positive fix applied) rather than the earlier, less accurate
    5-bucket inventory doc -- one source of truth for the name heuristic.
    Only rows whose category is a work-shaped one (not UNKNOWN, not
    NON_WORK/IGNORE) and whose id isn't already confirmed are included,
    as UNMAPPED_WORK."""
    text = UNMAPPED_WORK_SOURCE.read_text(encoding="utf-8")
    rows = []
    work_categories = {"COMPANY_JOB_INTAKE", "COMPANY_CLOSINGS", "TECH_DISPATCH", "TECH_CLOSINGS"}
    in_appendix = False
    for line in text.splitlines():
        if line.startswith("## 7. Full 372-group appendix"):
            in_appendix = True
            continue
        if not in_appendix or not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 6:
            continue
        group_id = cells[0].strip("` ")
        exact_name = cells[1]
        category = cells[2]
        company = cells[3]
        area = cells[4]
        if category not in work_categories:
            continue
        if group_id in confirmed_ids:
            continue
        rows.append({
            "group_id": group_id,
            "exact_name": exact_name,
            "role": "UNMAPPED_WORK",
            "is_active": True,
            "confirmed_by_owner": False,
            "source": "name_heuristic",
            "notes": (
                f"Offline heuristic guess (unconfirmed): {category}"
                + (f", company guess: {company}" if company != "-" else "")
                + (f", area guess: {area}" if area != "-" else "")
            ),
        })
    return rows


def seed(conn) -> dict:
    confirmed_ids = {c["group_id"] for c in CONFIRMED}
    counts = {"confirmed": 0, "unmapped_work": 0}
    for entry in CONFIRMED:
        entry = {**entry, "is_active": True, "confirmed_by_owner": True, "source": "owner_manual"}
        storage.upsert_group_registry_entry(conn, entry)
        counts["confirmed"] += 1
    for entry in _load_unmapped_candidates(confirmed_ids):
        storage.upsert_group_registry_entry(conn, entry)
        counts["unmapped_work"] += 1
    return counts


if __name__ == "__main__":
    db_path = db_safety.resolve_db_path_from_env()
    conn = storage.connect(db_path)
    result = seed(conn)
    print(f"Seeded registry: {result['confirmed']} confirmed, {result['unmapped_work']} unmapped_work "
          f"(against {db_path})")
