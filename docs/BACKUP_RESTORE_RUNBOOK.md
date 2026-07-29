# Backup / Restore Runbook

## Backup

```bash
python scripts/backup_database.py --db data/dispatcher.db
```

Uses `sqlite3.Connection.backup()` (SQLite's own online-backup API, not
a raw file copy) — takes a consistent snapshot even mid-transaction.
Writes to `../ai-dispatcher-db-backups/` (sibling of the repo, matching
this project's established convention) as
`dispatcher.db.pre-<label>-<UTC-timestamp>.bak`. Prints timestamp, byte
size, sha256 checksum, schema revision, integrity result, and row
counts — **never row content**.

## Verify a backup independently

```bash
python scripts/verify_backup.py <backup-path>
```

Opens the backup strictly read-only. Reports the same facts as backup
does, plus a final `PASS`/`FAIL`. Exit code 0 on pass, 1 on fail, 2 if
the file doesn't exist.

## Restore (temporary target only — never the live database)

```bash
python scripts/restore_database.py --source <backup-path> --target <temporary-path>
```

`restore_database.py` **physically refuses** to write to
`storage.DB_PATH` (`_reject_live_target()` raises `ValueError` before
any copy happens, regardless of what `--target` is passed) — restoring
over the live database is a separate, deliberate recovery procedure a
human performs manually (copy the backup file over the live path
themselves, with the app stopped), never something a script automates.
Also refuses an existing target path (never silently overwrites a
restore destination) and a source==target collision.

After restoring, the script verifies the copy: schema revision,
`integrity_check`, `foreign_key_check`, row counts — and opens it via a
`mode=ro` URI connection to prove "the application can open the
restored copy in read-only test mode" (an actual `INSERT` attempt
against that read-only handle is asserted to fail in
`tests/test_backup_restore.py::test_restored_copy_opens_read_only_without_error`).

## Restore drill performed this round

Executed against a **temporary, fabricated database** (never the real
one): seeded a job, backed it up, restored the backup to a fresh
temporary path, and confirmed:

- expected schema revision (`SCHEMA_REVISION` = 2, matching the source)
- expected row counts (1 job, matching the source)
- `integrity_check` = `ok`
- zero `foreign_key_check` violations
- the restored copy opens read-only and rejects a write attempt

See `tests/test_backup_restore.py` (16 tests) for the full, executable
proof — this was not a manual one-off, it runs on every test suite
execution.

## What was NOT done this round

No restore drill was run against the **real** database's backups
(disclosed, not silently skipped) — per this round's explicit
instruction not to touch the real database. Any of the existing
timestamped backups already sitting in `../ai-dispatcher-db-backups/`
(from prior rounds) can be verified independently at any time with:

```bash
python scripts/verify_backup.py "../ai-dispatcher-db-backups/<filename>.bak"
```

which is read-only and safe to run against them without restoring
anything.
