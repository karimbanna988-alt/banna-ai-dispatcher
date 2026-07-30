"""Explicit schema lifecycle management -- the only sanctioned way to
apply a database migration in this project. storage.connect() itself
never migrates a pre-existing database (see its own docstring); this
script is the human-operated boundary that does.

Usage:
    python scripts/manage_schema.py check [--db PATH]
    python scripts/manage_schema.py migrate [--db PATH] [--yes]
    python scripts/manage_schema.py verify [--db PATH]

check:
    Opens the database without applying any migration. Reports the
    current schema revision, the required revision, and any pending
    migrations by name. Prints no customer data or secrets. Exits 0 if
    up to date, 1 if an upgrade is required, 2 if the database doesn't
    exist.

migrate:
    Requires the database to already exist. Creates a timestamped
    backup first (via backup_database.py's same SQLite-aware backup
    routine), then applies storage.migrate_schema() -- idempotent, safe
    to re-run. Prompts for confirmation unless --yes is passed. Never
    runs automatically from anywhere else in this codebase.

verify:
    Checks required columns/indexes are present, runs
    PRAGMA integrity_check and PRAGMA foreign_key_check, confirms the
    partial unique idempotency index exists, and reports row counts
    only (never row contents).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backup_database  # noqa: E402 (sys.path set above)
from dispatcher import storage  # noqa: E402


def cmd_check(db_path: Path) -> int:
    if not db_path.exists():
        print(f"ERROR  Database not found at {db_path}")
        return 2
    conn = storage.connect(db_path)
    status = storage.check_schema_status(conn)
    shadow_ready = storage.shadow_schema_present(conn)
    conn.close()

    print(f"Current schema revision:  {status.current_revision}")
    print(f"Required schema revision: {status.required_revision}")
    print(f"Shadow Mode schema present (optional): {shadow_ready}")
    if status.up_to_date:
        print("Core schema is up to date. No core migration required.")
    else:
        print(f"Pending core migrations ({len(status.missing)}):")
        for item in status.missing:
            print(f"  - {item}")
    if not shadow_ready:
        print("Shadow Mode's schema is not yet installed -- optional, run migrate to add it.")
    if not status.up_to_date or not shadow_ready:
        print()
        print("Run to apply:")
        print(f"  python scripts/manage_schema.py migrate --db {db_path}")
    # Exit code reflects CORE readiness only -- Shadow Mode is an
    # optional capability layered on top of core revision 2 (see
    # storage.py's own _SCHEMA_MARKERS docstring); its absence must
    # never be reported as "the database needs an urgent migration" the
    # way a genuine core gap is.
    return 0 if status.up_to_date else 1


def cmd_migrate(db_path: Path, assume_yes: bool) -> int:
    if not db_path.exists():
        print(f"ERROR  Database not found at {db_path}")
        return 2

    conn = storage.connect(db_path)
    status = storage.check_schema_status(conn)
    shadow_ready = storage.shadow_schema_present(conn)
    if status.up_to_date and shadow_ready:
        conn.close()
        print("Schema is already up to date (core and Shadow Mode). Nothing to do.")
        return 0

    if not status.up_to_date:
        print(f"Pending core migrations ({len(status.missing)}):")
        for item in status.missing:
            print(f"  - {item}")
    if not shadow_ready:
        print("Pending optional migration: Shadow Mode schema (shadow_episodes, shadow_events)")
    print()

    if not assume_yes:
        answer = input(f"Back up and migrate {db_path}? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted -- no changes made.")
            conn.close()
            return 1

    backup_path = backup_database.backup_database(db_path)
    print(f"Backup created: {backup_path}")

    result = storage.migrate_schema(conn)
    shadow_ready_after = storage.shadow_schema_present(conn)
    conn.close()

    if result.up_to_date and shadow_ready_after:
        print(f"Migration complete. Core schema revision is now {result.current_revision}; Shadow Mode schema present: {shadow_ready_after}.")
        return 0
    print("Migration finished but the schema is still not fully up to date:")
    for item in result.missing:
        print(f"  - {item}")
    if not shadow_ready_after:
        print("  - Shadow Mode schema still missing")
    print("Re-run this command to resume -- migrate_schema() is idempotent and resumable.")
    return 1


def cmd_verify(db_path: Path) -> int:
    if not db_path.exists():
        print(f"ERROR  Database not found at {db_path}")
        return 2
    conn = storage.connect(db_path)
    status = storage.check_schema_status(conn)

    ok = True
    print(f"Schema up to date: {status.up_to_date}")
    if not status.up_to_date:
        ok = False
        for item in status.missing:
            print(f"  MISSING: {item}")

    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"integrity_check: {integrity}")
    ok = ok and integrity == "ok"

    fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    print(f"foreign_key_check violations: {len(fk_violations)}")
    ok = ok and not fk_violations

    index_row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_events_idempotency_key'"
    ).fetchone()
    print(f"idx_events_idempotency_key present: {index_row is not None}")
    ok = ok and index_row is not None

    # Informational only -- Shadow Mode is optional and never affects
    # the core VERIFY: PASS/FAIL verdict above.
    shadow_ready = storage.shadow_schema_present(conn)
    print(f"Shadow Mode schema present (optional): {shadow_ready}")
    if shadow_ready:
        shadow_episode_count = conn.execute("SELECT COUNT(*) FROM shadow_episodes").fetchone()[0]
        shadow_event_count = conn.execute("SELECT COUNT(*) FROM shadow_events").fetchone()[0]
        print(f"  shadow_episodes rows: {shadow_episode_count}")
        print(f"  shadow_events rows: {shadow_event_count}")

    print("Row counts:")
    for table in ("jobs", "events", "quo_pending_messages", "technicians"):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {count}")

    conn.close()
    print()
    print("VERIFY: PASS" if ok else "VERIFY: FAIL")
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--db", type=Path, default=storage.DB_PATH)

    migrate_parser = subparsers.add_parser("migrate")
    migrate_parser.add_argument("--db", type=Path, default=storage.DB_PATH)
    migrate_parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--db", type=Path, default=storage.DB_PATH)

    args = parser.parse_args(argv)
    if args.command == "check":
        return cmd_check(args.db)
    if args.command == "migrate":
        return cmd_migrate(args.db, args.yes)
    if args.command == "verify":
        return cmd_verify(args.db)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
