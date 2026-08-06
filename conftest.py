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
