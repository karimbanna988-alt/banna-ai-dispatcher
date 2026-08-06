"""Fail-closed database path resolution -- the single place that decides
which SQLite file a given run of the code is allowed to open.

storage.connect(None) legitimately defaults to the real production
database (storage.DB_PATH) -- that default exists on purpose, for the
one sanctioned live path (scripts/start_crm.ps1 -> `python
operator_api.py` with no overrides). The bug this module exists to
prevent is everything else silently landing on that same default: a
demo-seeding script or a test that forgets to set OPERATOR_API_DB_PATH
must fail loudly, not fall through to the real database. See
docs/CURRENT_SPRINT_CHECKPOINT.md for the incident that motivated this.

Nothing in this module opens a connection itself -- it only decides
whether a requested path is safe to hand to storage.connect(), for a
declared mode ("live", "demo", or "test").
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from . import storage

DEMO_MODE_ENV_VAR = "LUXOR_DEMO_MODE"  # set by scripts/start_demo.ps1 only -- never for a normal/real run
DB_PATH_ENV_VAR = "OPERATOR_API_DB_PATH"


class UnsafeDatabasePathError(RuntimeError):
    """Raised when demo/test code would otherwise touch the production
    database -- either by omitting a path entirely (silent fallback) or
    by passing a path that resolves to the real one."""


VALID_MODES = ("live", "demo", "test")


def resolve_db_path(mode: str, requested: Optional[Path]) -> Optional[Path]:
    """Returns the Path storage.connect() should use for `mode`, or None
    to mean "let storage.connect() use its own default" -- only ever
    returned for mode="live", since that default IS the production
    database and only the live path may use it implicitly.

    demo/test: `requested` must be given and must not resolve to the
    production database path -- raises UnsafeDatabasePathError otherwise.
    Comparison is via Path.resolve() (canonical, symlink-following),
    never a raw string comparison, so a relative path, a different
    drive-letter casing, or a path reached through a symlink/junction
    that ultimately points at the real file is still caught.
    """
    if mode not in VALID_MODES:
        raise ValueError(f"unknown db-safety mode: {mode!r} -- must be one of {VALID_MODES}")

    if mode == "live":
        return requested

    # demo / test -- no silent fallback, ever.
    if requested is None:
        raise UnsafeDatabasePathError(
            f"{mode} mode requires an explicit database path -- refusing to fall back "
            f"to the production database ({storage.DB_PATH})."
        )
    requested = Path(requested)
    real = storage.DB_PATH.resolve()
    resolved = requested.resolve()
    if resolved == real:
        raise UnsafeDatabasePathError(
            f"{mode} mode refuses to use the production database path ({real})."
        )
    return requested


def current_mode() -> str:
    """"test" whenever running under pytest (regardless of whether the
    calling test remembered to set OPERATOR_API_DB_PATH -- catching that
    omission is the whole point), else "demo" when LUXOR_DEMO_MODE is
    set, else "live". Shared by operator_api.py and
    whatsapp_bridge_adapter.py so there is exactly one place this
    detection logic lives, not two independently-maintained copies."""
    if "pytest" in sys.modules:
        return "test"
    if os.environ.get(DEMO_MODE_ENV_VAR, "").lower() == "true":
        return "demo"
    return "live"


def resolve_db_path_from_env() -> Optional[Path]:
    """Convenience wrapper: reads OPERATOR_API_DB_PATH and the current
    mode from the process environment and resolves them together -- the
    exact pair of steps operator_api.py::_db_path() and
    whatsapp_bridge_adapter.py::_db_path() each need."""
    override = os.environ.get(DB_PATH_ENV_VAR)
    requested = Path(override) if override else None
    return resolve_db_path(current_mode(), requested)
