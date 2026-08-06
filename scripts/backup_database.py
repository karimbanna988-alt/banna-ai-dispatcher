"""SQLite-aware database backup.

Uses sqlite3's own `Connection.backup()` API (not a raw file copy) --
it takes a consistent snapshot even if the source has an open
transaction or a WAL file sitting alongside it, which a plain
`shutil.copy` cannot guarantee. Never overwrites the live database.
Prints no customer row content -- only timestamp, byte size, checksum,
schema revision, and integrity result.

Usage:
    python scripts/backup_database.py [--db PATH] [--dest-dir DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dispatcher import storage

DEFAULT_BACKUP_DIR = Path(__file__).resolve().parents[2] / "ai-dispatcher-db-backups"

# Conservative default: keep a lot of history rather than risk deleting a
# backup someone still needs. Retention only ever touches files matching
# this exact "<source db name>.pre-*-*.bak" pattern in the destination
# directory -- it never looks at, counts, or deletes anything else there.
DEFAULT_RETENTION_COUNT = 30


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_database(source_path: Path, dest_dir: Path = None, label: str = "manual") -> Path:
    """Backs up `source_path` into `dest_dir` (default
    DEFAULT_BACKUP_DIR, sibling to the repo, matching this project's
    established convention) using sqlite3's own online backup API.
    Returns the backup file's path. Refuses if source and computed
    destination would resolve to the same file."""
    if not source_path.exists():
        raise FileNotFoundError(f"Source database not found: {source_path}")

    dest_dir = dest_dir or DEFAULT_BACKUP_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_path = dest_dir / f"{source_path.name}.pre-{label}-{timestamp}.bak"

    if dest_path.resolve() == source_path.resolve():
        raise ValueError("Backup destination must not be the same file as the source database.")

    source_conn = sqlite3.connect(source_path)
    dest_conn = sqlite3.connect(dest_path)
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        source_conn.close()

    return dest_path


def apply_retention(dest_dir: Path, source_name: str, keep: int = DEFAULT_RETENTION_COUNT) -> list[Path]:
    """Deletes the oldest backups of `source_name` in `dest_dir` beyond
    the most recent `keep`. Only ever considers files matching this
    project's own "<source_name>.pre-<label>-<timestamp>.bak" naming
    convention -- never touches any other file. `keep <= 0` disables
    retention (nothing is deleted). Returns the list of deleted paths."""
    if keep <= 0:
        return []
    candidates = sorted(dest_dir.glob(f"{source_name}.pre-*.bak"), key=lambda p: p.stat().st_mtime)
    excess = candidates[:-keep] if len(candidates) > keep else []
    deleted = []
    for path in excess:
        path.unlink()
        deleted.append(path)
    return deleted


def describe_backup(backup_path: Path) -> dict:
    """Read-only metadata about a backup -- never row content."""
    conn = sqlite3.connect(f"file:{backup_path.as_posix()}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        schema_status = storage.check_schema_status(conn)
        row_counts = {}
        for table in ("jobs", "events", "quo_pending_messages", "technicians"):
            try:
                row_counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.OperationalError:
                row_counts[table] = None
    finally:
        conn.close()
    return {
        "path": str(backup_path),
        "size_bytes": backup_path.stat().st_size,
        "sha256": _sha256(backup_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "integrity_check": integrity,
        "foreign_key_violations": len(fk_violations),
        "schema_current_revision": schema_status.current_revision,
        "schema_required_revision": schema_status.required_revision,
        "schema_up_to_date": schema_status.up_to_date,
        "row_counts": row_counts,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=storage.DB_PATH)
    parser.add_argument("--dest-dir", type=Path, default=None)
    parser.add_argument("--label", default="manual")
    parser.add_argument(
        "--retain-count", type=int, default=DEFAULT_RETENTION_COUNT,
        help=f"Keep only the most recent N backups of this database in --dest-dir (default {DEFAULT_RETENTION_COUNT}). "
             "0 disables retention -- nothing is ever deleted.",
    )
    args = parser.parse_args(argv)

    backup_path = backup_database(args.db, args.dest_dir, args.label)
    info = describe_backup(backup_path)
    for key, value in info.items():
        print(f"{key}: {value}")

    deleted = apply_retention(backup_path.parent, args.db.name, args.retain_count)
    if deleted:
        print(f"retention: removed {len(deleted)} backup(s) older than the most recent {args.retain_count}")
    else:
        print("retention: nothing removed")

    return 0 if info["integrity_check"] == "ok" and info["foreign_key_violations"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
