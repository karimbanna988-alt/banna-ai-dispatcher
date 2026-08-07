import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import pytest  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _real_database_untouched_tripwire():
    """Last-resort, whole-suite safety net -- independent of
    dispatcher.db_safety's own guards (which this fixture does not
    replace; see tests/test_db_safety.py and the operator_api.py/
    whatsapp_bridge_adapter.py tests for those). If the real database's
    mtime or size changes at any point during a full test run, something
    in this suite wrote to it -- that is always a bug, regardless of
    which code path caused it, so this fails the run loudly rather than
    letting it pass silently."""
    from dispatcher import storage

    db_path = storage.DB_PATH
    before = db_path.stat() if db_path.exists() else None
    yield
    after = db_path.stat() if db_path.exists() else None
    if before is None and after is None:
        return
    assert before is not None and after is not None, (
        "data/dispatcher.db existed before the test session but not after (or vice versa) -- "
        "investigate immediately, do not ignore."
    )
    assert (before.st_mtime, before.st_size) == (after.st_mtime, after.st_size), (
        "data/dispatcher.db changed during this test session -- some test wrote to the real "
        "database. This must never happen; find and fix the offending test before merging."
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
