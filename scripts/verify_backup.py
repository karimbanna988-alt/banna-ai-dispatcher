"""Independently verify a backup file -- opened strictly read-only, so
this script cannot itself write to the backup or the live database.

Usage:
    python scripts/verify_backup.py <backup-path>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backup_database import describe_backup  # noqa: E402 (sys.path set above)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("backup_path", type=Path)
    args = parser.parse_args(argv)

    if not args.backup_path.exists():
        print(f"ERROR  Backup not found at {args.backup_path}")
        return 2

    info = describe_backup(args.backup_path)
    for key, value in info.items():
        print(f"{key}: {value}")

    ok = info["integrity_check"] == "ok" and info["foreign_key_violations"] == 0
    print()
    print("VERIFY: PASS" if ok else "VERIFY: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
