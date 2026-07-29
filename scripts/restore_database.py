"""Restore a backup to an explicit, non-live target path -- never the
real database. This script physically refuses to write to
storage.DB_PATH under any circumstance (see _reject_live_target()
below); restoring over the real database is a separate, deliberate
recovery procedure a human performs manually, not something any script
in this codebase automates.

Usage:
    python scripts/restore_database.py --source <backup-path> --target <temporary-path>
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dispatcher import storage


def _reject_live_target(target: Path) -> None:
    if target.resolve() == storage.DB_PATH.resolve():
        raise ValueError(
            "Refusing to restore over the live database (storage.DB_PATH). "
            "Restore to a temporary path and follow a separate, deliberate "
            "recovery procedure if you actually intend to replace the live file."
        )


def restore_database(source: Path, target: Path) -> Path:
    if not source.exists():
        raise FileNotFoundError(f"Backup source not found: {source}")
    if source.resolve() == target.resolve():
        raise ValueError("Source and target must not be the same file.")
    _reject_live_target(target)
    if target.exists():
        raise FileExistsError(f"Target already exists: {target} -- choose a fresh path.")

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def verify_restored_copy(target: Path) -> dict:
    """Opens the restored copy read-only (the "application can open the
    restored copy in read-only test mode" requirement) and reports
    schema/integrity/row-count facts -- never row content."""
    conn = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    try:
        status = storage.check_schema_status(conn)
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        row_counts = {}
        for table in ("jobs", "events", "quo_pending_messages", "technicians"):
            try:
                row_counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.OperationalError:
                row_counts[table] = None
    finally:
        conn.close()
    return {
        "schema_current_revision": status.current_revision,
        "schema_required_revision": status.required_revision,
        "schema_up_to_date": status.up_to_date,
        "integrity_check": integrity,
        "foreign_key_violations": len(fk_violations),
        "row_counts": row_counts,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        restored = restore_database(args.source, args.target)
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        print(f"ERROR  {exc}")
        return 1

    print(f"Restored: {restored}")
    info = verify_restored_copy(restored)
    for key, value in info.items():
        print(f"{key}: {value}")

    ok = info["integrity_check"] == "ok" and info["foreign_key_violations"] == 0
    print()
    print("RESTORE VERIFY: PASS" if ok else "RESTORE VERIFY: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
