import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import pytest  # noqa: E402


# Tables the LIVE receive-only shadow stack legitimately appends to while
# the suite runs -- the WhatsApp bridge and Quo poller are separate
# processes that keep capturing during a release test run, and stopping
# them to test would mean losing real operational traffic.
#
# These are observation/audit tables only. No business truth is in this
# set: jobs, events, technicians and whatsapp_group_registry are
# deliberately absent, so a test touching any of them still fails hard.
_LIVE_APPEND_ONLY_TABLES = frozenset({
    "whatsapp_pending_observations",
    "whatsapp_outgoing_audit",
    "quo_pending_messages",
    "quo_processed_messages",
    "quo_poll_state",
})


def _snapshot_real_db(db_path, bounds=None):
    """(per-table fingerprint) of the real database, read-only.

    `bounds` is {table: max_rowid} and matters only for the append-only
    tables: the "after" snapshot must fingerprint exactly the same row
    range the "before" snapshot did, otherwise a legitimately-appended
    row would change the digest and look like tampering. Pass the before
    snapshot's max_ids to compare like with like."""
    import hashlib
    import sqlite3

    if not db_path.exists():
        return None
    bounds = bounds or {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        snap = {}
        for table in sorted(tables):
            try:
                max_id = conn.execute(f"SELECT COALESCE(MAX(rowid), 0) FROM '{table}'").fetchone()[0]
                has_rowid = True
            except sqlite3.OperationalError:
                has_rowid, max_id = False, None
            if has_rowid and table in _LIVE_APPEND_ONLY_TABLES:
                limit = bounds.get(table, max_id)
                rows = conn.execute(
                    f"SELECT * FROM '{table}' WHERE rowid <= ? ORDER BY rowid", (limit,)).fetchall()
            else:
                rows = conn.execute(f"SELECT * FROM '{table}'").fetchall()
            digest = hashlib.sha256(repr(rows).encode("utf-8", "replace")).hexdigest()
            snap[table] = {"max_id": max_id, "digest": digest, "count": len(rows)}
        return snap
    finally:
        conn.close()


@pytest.fixture(autouse=True, scope="session")
def _real_database_untouched_tripwire():
    """Last-resort, whole-suite safety net -- independent of
    dispatcher.db_safety's own guards (which this fixture does not
    replace; see tests/test_db_safety.py and the operator_api.py/
    whatsapp_bridge_adapter.py tests for those). If any test writes to
    the real database, this fails the run loudly rather than letting it
    pass silently.

    It compares TABLE CONTENT rather than file mtime/size. The earlier
    file-stat version could not tell "a test wrote to production" (always
    a bug) from "the live WhatsApp bridge captured a real message during
    the run" (correct, and the whole point of the shadow system), so a
    release run could not produce an unambiguous result while shadowing
    was live.

    This is not a relaxation. Business tables -- jobs, events,
    technicians, whatsapp_group_registry -- must be byte-identical, which
    is a stricter statement than "the file size matched". Observation
    tables may only GAIN rows: every row that existed beforehand must be
    unchanged, so a test that edits an existing observation still fails.
    """
    from dispatcher import storage

    db_path = storage.DB_PATH
    before = _snapshot_real_db(db_path)
    yield
    # Fingerprint the same row range the "before" snapshot covered, so a
    # legitimately appended observation cannot look like tampering.
    bounds = {t: v["max_id"] for t, v in (before or {}).items() if v["max_id"] is not None}
    after = _snapshot_real_db(db_path, bounds=bounds)

    if before is None and after is None:
        return
    assert before is not None and after is not None, (
        "data/dispatcher.db existed before the test session but not after (or vice versa) -- "
        "investigate immediately, do not ignore."
    )
    assert set(before) == set(after), (
        f"tables appeared/disappeared during the test session: "
        f"{set(before) ^ set(after)} -- a test altered the real schema."
    )
    for table, was in before.items():
        now = after[table]
        if table in _LIVE_APPEND_ONLY_TABLES:
            assert now["digest"] == was["digest"], (
                f"{table}: rows that already existed were MODIFIED during the test session. "
                "Live capture only ever appends, so this means a test wrote to the real database."
            )
            assert now["max_id"] >= was["max_id"], (
                f"{table}: rows were deleted during the test session -- a test wrote to the "
                "real database."
            )
        else:
            assert now["digest"] == was["digest"], (
                f"{table}: contents changed during the test session -- a test wrote to real "
                "business data. This must never happen; find and fix the offending test."
            )


@pytest.fixture(autouse=True)
def _reset_operator_api_rate_limit_state():
    """operator_api.py's _LOGIN_ATTEMPTS/_ACTIVE_SESSIONS are deliberately
    in-memory, module-level state (a server restart is meant to act as
    "log everyone out" -- see their own comments in operator_api.py) --
    but that means they silently persist across tests within the same
    pytest process, making test order matter when it shouldn't. Observed
    once as a real flake: test_post_without_csrf_token_rejected passed
    every time in isolation but failed intermittently as part of the
    full suite, traced to a nearby test exhausting the login rate limit
    for the same client_ip within the same LOGIN_WINDOW_SECONDS window.
    Clearing both before and after every test makes the suite
    order-independent without weakening the real runtime throttling
    itself (a real running server's state still accumulates normally;
    only the test suite's shared process state is reset)."""
    try:
        import operator_api
    except ImportError:
        yield
        return
    operator_api._LOGIN_ATTEMPTS.clear()
    operator_api._ACTIVE_SESSIONS.clear()
    yield
    operator_api._LOGIN_ATTEMPTS.clear()
    operator_api._ACTIVE_SESSIONS.clear()


@pytest.fixture(autouse=True)
def _no_ambient_quo_api_key(monkeypatch):
    """Fail-closed by construction, not by convention: no test may rely
    on QUO_API_KEY being absent from the environment -- it must be
    guaranteed absent. Discovered the hard way: a test whose only
    guarantee was a code comment ("fails with 400 in this test env (no
    QUO_API_KEY)") started making a real, ~15-30s-timeout network call
    to api.quo.com using a real key, the moment that key happened to be
    set in the ambient shell (e.g. inherited from a real .env-backed
    environment) -- indistinguishable from a flaky test until traced to
    the root cause. Any test that genuinely needs QUO_API_KEY set can
    still call monkeypatch.setenv("QUO_API_KEY", ...) itself -- that
    happens after this fixture's delenv in the same test's teardown
    stack, so it is not blocked, just no longer accidentally inherited."""
    monkeypatch.delenv("QUO_API_KEY", raising=False)
