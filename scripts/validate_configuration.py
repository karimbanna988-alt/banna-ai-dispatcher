"""Deterministic configuration validator.

Reports operational configuration problems -- technician roster gaps,
unresolvable routing, missing per-source policy coverage -- without
printing any customer data (no raw messages, phone numbers, addresses,
or names beyond a technician's own roster name) and without ever
writing to the database: the connection is opened strictly read-only
(SQLite's own `mode=ro` URI flag), so an accidental write anywhere in
this script's call chain would raise rather than silently succeed.

Usage:
    python scripts/validate_configuration.py
    python scripts/validate_configuration.py --db path/to/other.db

Exit code is 1 if any ERROR-level finding exists, 0 otherwise (WARNING/
INFO findings never fail the run).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dispatcher import storage
from dispatcher.models import Source
from dispatcher.policy import is_policy_configured
from dispatcher.rules.market_aliases import resolve_market_and_area
from dispatcher.rules.service_areas import load_service_areas
from dispatcher.storage import MessageType


@dataclass(frozen=True)
class Finding:
    level: str  # "ERROR" | "WARNING" | "INFO"
    area: str
    message: str


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _known_area_labels(service_areas) -> set:
    labels = set()
    for rec in service_areas:
        if rec.city:
            labels.add(rec.city)
        if rec.state:
            labels.add(rec.state)
    return labels


def _validate_technicians(conn: sqlite3.Connection, known_labels: set) -> List[Finding]:
    findings: List[Finding] = []
    technicians = storage.list_technicians(conn, active_only=False)
    seen_names_ci: dict = {}

    for tech in technicians:
        label = f"technician id={tech['id']}"
        if tech["active"] and not tech["areas"]:
            findings.append(Finding("ERROR", "technicians", f"{label} is active but has no configured service areas."))
        for area in tech["areas"]:
            if area not in known_labels:
                findings.append(
                    Finding("ERROR", "technicians", f"{label} lists unknown service area {area!r} (not in rules/service_areas.yaml).")
                )
        if tech["active"] and not tech["dispatch_group"]:
            findings.append(Finding("WARNING", "technicians", f"{label} is active but has no dispatch_group (WhatsApp destination) configured."))
        if tech["active"] and not tech["closing_group"]:
            findings.append(Finding("INFO", "technicians", f"{label} is active but has no closing_group configured."))

        name_ci = tech["name"].strip().lower()
        if name_ci in seen_names_ci:
            findings.append(
                Finding("WARNING", "technicians", f"technician name {tech['name']!r} (id={tech['id']}) duplicates id={seen_names_ci[name_ci]} case-insensitively.")
            )
        else:
            seen_names_ci[name_ci] = tech["id"]

    active_count = sum(1 for t in technicians if t["active"])
    if active_count == 0:
        findings.append(Finding("ERROR", "technicians", "No active technicians configured at all."))

    configured_dispatch = sum(1 for t in technicians if t["active"] and t["dispatch_group"])
    if active_count > 0 and configured_dispatch == 0:
        findings.append(
            Finding("ERROR", "technicians", "No active technician has a dispatch_group configured -- WhatsApp dispatch is blocked for every job.")
        )

    return findings


def _validate_routing(conn: sqlite3.Connection) -> List[Finding]:
    findings: List[Finding] = []

    greenville_lockout = storage.list_eligible_technicians_for(conn, "Greenville", "car lockout")
    if not greenville_lockout:
        findings.append(Finding("WARNING", "routing", "No eligible technician for Greenville car lockouts."))

    greenville_keys = storage.list_eligible_technicians_for(conn, "Greenville", "keys")
    if not greenville_keys:
        findings.append(Finding("WARNING", "routing", "No eligible technician for Greenville keys/lock-installation."))

    savannah = storage.list_eligible_technicians_for(conn, "Savannah")
    if not savannah:
        findings.append(Finding("WARNING", "routing", "No eligible technician for Savannah (generic)."))

    atlantic_city = resolve_market_and_area(None, "Atlantic City Locksmith Pro")
    if atlantic_city.canonical_area != "NJ":
        findings.append(
            Finding("ERROR", "routing", f"Atlantic City market alias resolves to {atlantic_city.canonical_area!r}, expected 'NJ'.")
        )

    return findings


def _validate_source_policies() -> List[Finding]:
    findings: List[Finding] = []
    for source in (Source.AGN, Source.LUCKY, Source.MAIN_LEADS):
        if not is_policy_configured(source.value, MessageType.NEW.value):
            findings.append(Finding("ERROR", "policy", f"No explicit NEW-job policy configured for {source.value}."))
    return findings


def export_blank_configuration_template(conn: sqlite3.Connection) -> str:
    """A CSV listing every real technician's id/name/current areas, with
    dispatch_group/closing_group left blank for a human to fill in and
    apply via the existing technician-management path (storage.py's
    add_technician/set_technician_active -- this script never writes).
    Contains technician ids/names only -- never a customer record, never
    a secret. A human decides the actual destination values; this
    script never fabricates or fills one in."""
    import csv
    import io

    technicians = storage.list_technicians(conn, active_only=False)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["technician_id", "name", "areas", "dispatch_group", "closing_group"])
    for tech in technicians:
        writer.writerow([tech["id"], tech["name"], ";".join(tech["areas"]), "", ""])
    return buffer.getvalue()


def validate(conn: sqlite3.Connection) -> List[Finding]:
    service_areas = load_service_areas()
    known_labels = _known_area_labels(service_areas)
    findings: List[Finding] = []
    findings += _validate_technicians(conn, known_labels)
    findings += _validate_routing(conn)
    findings += _validate_source_policies()
    return findings


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=storage.DB_PATH, help="Path to the SQLite database (opened strictly read-only)")
    parser.add_argument(
        "--export-template", type=Path, default=None,
        help="Write a blank technician-destination CSV template (id/name/areas, empty dispatch_group/closing_group) to this path instead of validating",
    )
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"ERROR   config       Database not found at {args.db}")
        return 2

    if args.export_template is not None:
        conn = _connect_readonly(args.db)
        try:
            csv_text = export_blank_configuration_template(conn)
        finally:
            conn.close()
        args.export_template.write_text(csv_text, encoding="utf-8")
        print(f"Template written to {args.export_template}")
        return 0

    conn = _connect_readonly(args.db)
    try:
        findings = validate(conn)
    finally:
        conn.close()

    for f in findings:
        print(f"{f.level:<7} {f.area:<12} {f.message}")

    errors = [f for f in findings if f.level == "ERROR"]
    warnings = [f for f in findings if f.level == "WARNING"]
    infos = [f for f in findings if f.level == "INFO"]
    print()
    print(f"Summary: {len(errors)} error(s), {len(warnings)} warning(s), {len(infos)} info.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
