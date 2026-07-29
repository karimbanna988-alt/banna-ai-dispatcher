# SQLite Connection Policy

## Facts established before deciding anything

- **App and poller use the same local database file.** `luxor_desk.py`
  (via `storage.connect()`) and `scripts/background_poller.py` (also
  via `storage.connect()`) both open `data/dispatcher.db` directly —
  confirmed by reading both files' connection code.
- **Every process runs on the same machine.** This is a local desktop
  tool (Streamlit + an optional standalone poller script, both started
  manually by the dispatcher on their own laptop) — no network
  filesystem, no multi-host deployment anywhere in this codebase.
- **Journal mode: SQLite's default (rollback journal / `DELETE` mode).**
  Never changed by this codebase. Confirmed: no `PRAGMA journal_mode`
  statement exists anywhere in `storage.py` before this round, and none
  was added.
- **Lock behavior: SQLite's default** — one writer at a time, readers
  don't block each other, a writer blocks other writers until it
  commits. Before this round, no `busy_timeout` was set, so any writer
  colliding with another writer's open transaction failed *immediately*
  with `sqlite3.OperationalError: database is locked`, uncaught.
- **Expected concurrent writers: at most two.** The Streamlit UI
  (one dispatcher, one browser tab in practice) and the optional
  background poller. Never more than a handful of near-simultaneous
  writes even in the worst case (a dispatcher clicking a button at the
  exact instant the poller commits a batch).

## Decision: keep the rollback journal, add `busy_timeout`

**WAL was not adopted.** Reasoning, applying the instruction's own
test ("is WAL appropriate for this local deployment?"): WAL's main
benefit is letting readers proceed without blocking on a writer — this
app has no read-heavy/write-heavy split that benefits from that (a
Streamlit page render does a handful of quick reads then returns; there
is no long-running reader that a writer would otherwise starve). WAL
also introduces two more files alongside the database
(`-wal`, `-shm`) that a naive `shutil.copy`-based backup would miss
(this codebase already avoids that trap — `scripts/backup_database.py`
uses SQLite's own `Connection.backup()` API, which handles WAL
correctly if it were ever enabled — but there's no forcing reason to
add that complexity now). **WAL still only permits one writer at a
time** regardless — it would not have solved the actual problem
(an uncaught lock error), only reduced how often contention happens.

**The actual problem — an immediate, uncaught lock error on any
contention at all — is fixed by `PRAGMA busy_timeout`, set on every
connection in `storage.connect()`:**

```python
storage.BUSY_TIMEOUT_MS = 5000
conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
```

A writer that collides with another writer's open transaction now
waits up to 5 seconds (SQLite's own internal busy-retry loop) before
raising, instead of failing on contact. Five seconds comfortably covers
one command's transaction (a few milliseconds in practice) with a wide
margin, without masking a genuinely stuck process for an excessive
window. Verified by `tests/test_concurrency.py::test_busy_timeout_absorbs_normal_contention_without_uncaught_lock_error`
(one thread holds a write lock for up to 2 seconds; a concurrent
command on a second connection must succeed, not raise).

## The rest of the connection policy

| Concern | Decision |
|---|---|
| `foreign_keys` | Per-connection (SQLite doesn't persist it) — enabled only after `_configure_foreign_key_enforcement()` confirms no orphan rows exist; left off (safe default) otherwise. Unchanged this round except for tolerating a not-yet-migrated schema (see `docs/SCHEMA_MIGRATION_RUNBOOK.md`). |
| Transaction mode | Python's `sqlite3` default (`isolation_level=""`) — implicit transaction before the first DML statement of a batch, explicit `conn.commit()`/`conn.rollback()`. `_insert_operational_event()` uses an explicit `BEGIN`/`COMMIT`/`rollback-on-exception` block for its multi-statement command transactions. |
| Row factory | Default (`sqlite3.Row`-less tuples) — unchanged; every function already builds its own `dict(zip(columns, row))` where a mapping is wanted, matching the existing codebase-wide convention. |
| Error translation | None added — `sqlite3.OperationalError`/`IntegrityError` propagate as-is; `DispatcherService` commands catch only the domain-specific `InvalidJobActionError`, never a raw SQLite exception (a raw one propagating up is a genuine bug signal, not something to swallow). |
| Connection lifetime | One connection per Streamlit page render (`conn = storage.connect()` at the top, `conn.close()` at the bottom) — unchanged, pre-existing convention. |
| Thread/process assumptions | `check_same_thread=False` (Streamlit reruns on a fresh thread each time) — pre-existing; each *process* (the Streamlit app, the standalone poller) opens its own independent connection object, never shared across processes. |
| `synchronous` | SQLite's default (`FULL` for rollback-journal mode) — never changed; this is the safest setting and this app has no throughput requirement that would justify trading durability for speed. |

## What this round tested

Two real, independent `sqlite3.Connection` objects to the same
temporary file, driven from two threads synchronized with a
`threading.Barrier` (never a sleep) — see `tests/test_concurrency.py`:
identical command race, distinct-command race, stale-update-racing-
completion, cancellation-racing-closing, and the `busy_timeout`
absorption test above. All five pass consistently across repeated runs.

**Not tested this round** (disclosed, not silently skipped): poller-
ingestion racing a dispatcher action, and a configuration-edit racing
dispatch preparation. Both are lower-risk than the tested scenarios
(ingestion only ever inserts new `quo_pending_messages` rows, which
share no lock contention with `events`/`jobs` writes in practice; there
is no configuration-edit write path yet — `scripts/validate_configuration.py`
remains read-only) — named here as remaining scope rather than omitted
silently.
