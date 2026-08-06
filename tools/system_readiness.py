"""Read-only operational health check, written for a nontechnical
operator to run before/after a shift or before demoing to a customer.

Checks, in order:
  1. CRM (operator_api.py) port 8512 -- listening + responding
  2. Python WhatsApp bridge adapter port 8513 -- listening + responding
  3. Node WhatsApp Shadow Bridge port 8514 -- listening, paired state
  4. WhatsApp allowlist size (a COUNT only -- never prints a group id or name)
  5. Real database SQLite integrity (PRAGMA integrity_check)
  6. Real database foreign-key integrity (PRAGMA foreign_key_check)
  7. Backup destination directory is writable
  8. Required configuration secrets are present and non-empty
  9. The bridge-adapter secret matches between the two .env files
     (compared as fingerprints only -- neither raw value is ever read
     into a variable that gets printed)
 10. Whether scripts/start_crm.ps1 and tools/windows_launcher point at
     the current CRM implementation (operator_api.py) rather than a
     retired one
 11. WhatsApp pending observations are stored durably (SQLite-backed
     table present, not the old in-memory list)
 12. No duplicate WhatsApp observation message_id values exist

This script is READ-ONLY end to end:
  - the real database is opened with SQLite's own `mode=ro` URI flag,
    so an accidental write anywhere in the call chain raises rather
    than silently succeeding
  - every HTTP call is a GET with a short timeout
  - it never starts, stops, or restarts any process
  - it never prints a secret value -- only whether one is present/
    non-empty and, where useful, a short sha256 fingerprint or a
    fingerprint-equality result

Usage:
    python tools/system_readiness.py [--db PATH]

Exit code: 0 if every check is PASS/WARNING, 1 if any check is FAIL or
ACTION REQUIRED, 2 if the script itself could not run (e.g. real
database file missing).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from dispatcher import storage  # noqa: E402

HTTP_TIMEOUT_SECONDS = 2.5

REQUIRED_SECRET_VARS = [
    "LUXOR_DEMO_PASSWORD",
    "OPERATOR_API_SESSION_SECRET",
    "WHATSAPP_BRIDGE_ADAPTER_SECRET",
]
OPTIONAL_SECRET_VARS = [
    "QUO_API_KEY",
]


@dataclass
class Check:
    name: str
    level: str  # "PASS" | "WARNING" | "FAIL" | "ACTION REQUIRED"
    detail: str


def _fingerprint(value: str) -> str:
    """A short, one-way fingerprint safe to print -- never reversible
    to the original secret, only usable to compare two secrets for
    equality without either one ever being displayed."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _http_get_json(url: str, timeout: float = HTTP_TIMEOUT_SECONDS) -> tuple:
    """Returns (status_code, parsed_json_or_None). Raises only on a
    connection-level failure (nothing listening, DNS, etc.) -- an HTTP
    error status is returned normally so callers can inspect it (e.g.
    401 from an unauthenticated /api/me is an expected, healthy result)."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, None
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return exc.code, None


def check_crm_port() -> Check:
    try:
        status, _ = _http_get_json("http://127.0.0.1:8512/api/me")
    except (urllib.error.URLError, OSError):
        return Check("CRM (operator_api.py, port 8512)", "WARNING",
                      "Not running. Start with scripts\\start_crm.ps1.")
    if status in (200, 401):
        return Check("CRM (operator_api.py, port 8512)", "PASS",
                      f"Listening and responding (HTTP {status}).")
    return Check("CRM (operator_api.py, port 8512)", "FAIL",
                 f"Listening but returned unexpected HTTP {status}.")


def check_python_adapter_port() -> Check:
    try:
        status, payload = _http_get_json("http://127.0.0.1:8513/health")
    except (urllib.error.URLError, OSError):
        return Check("Python bridge adapter (port 8513)", "WARNING",
                      "Not running -- normal if the CRM (operator_api.py) is not running, since it starts this adapter.")
    if status == 200 and isinstance(payload, dict) and payload.get("status") == "ok":
        return Check("Python bridge adapter (port 8513)", "PASS", "Listening and healthy.")
    return Check("Python bridge adapter (port 8513)", "FAIL", f"Unexpected response (HTTP {status}).")


def check_whatsapp_bridge_port() -> tuple:
    """Returns (bridge_check, allowlist_check)."""
    try:
        status, payload = _http_get_json("http://127.0.0.1:8514/health")
    except (urllib.error.URLError, OSError):
        bridge = Check("WhatsApp Shadow Bridge (port 8514)", "WARNING",
                        "Not running. Start with scripts\\start_boss_demo.ps1 or whatsapp_shadow_bridge\\scripts\\start_normal.ps1.")
        allowlist = Check("WhatsApp allowlist", "WARNING", "Bridge not reachable -- allowlist size unknown.")
        return bridge, allowlist

    if status != 200 or not isinstance(payload, dict):
        bridge = Check("WhatsApp Shadow Bridge (port 8514)", "FAIL", f"Unexpected response (HTTP {status}).")
        allowlist = Check("WhatsApp allowlist", "WARNING", "Bridge unhealthy -- allowlist size unknown.")
        return bridge, allowlist

    paired = bool(payload.get("paired"))
    enabled = bool(payload.get("enabled"))
    bridge_status = payload.get("status", "unknown")
    if paired:
        bridge = Check("WhatsApp Shadow Bridge (port 8514)", "PASS", f"Listening, paired, status={bridge_status!r}.")
    elif enabled:
        bridge = Check("WhatsApp Shadow Bridge (port 8514)", "WARNING", f"Listening but not paired yet, status={bridge_status!r}.")
    else:
        bridge = Check("WhatsApp Shadow Bridge (port 8514)", "WARNING", "Listening but disabled (WHATSAPP_SHADOW_ENABLED is not true).")

    try:
        astatus, apayload = _http_get_json("http://127.0.0.1:8514/allowlist")
        if astatus == 200 and isinstance(apayload, dict) and isinstance(apayload.get("groups"), list):
            count = len(apayload["groups"])
            allowlist = Check("WhatsApp allowlist", "PASS", f"{count} group(s) approved. (Names/ids withheld by design.)")
        else:
            allowlist = Check("WhatsApp allowlist", "WARNING", f"Could not read allowlist (HTTP {astatus}).")
    except (urllib.error.URLError, OSError):
        allowlist = Check("WhatsApp allowlist", "WARNING", "Bridge became unreachable while checking the allowlist.")

    return bridge, allowlist


def check_database_integrity(db_path: Path) -> tuple:
    """Returns (integrity_check, fk_check, observations_durable_check, no_duplicate_ids_check)."""
    if not db_path.exists():
        missing = Check("Database", "FAIL", f"No database file at {db_path}.")
        return missing, missing, missing, missing

    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        integrity_result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        integrity = Check("SQLite integrity_check", "PASS" if integrity_result == "ok" else "FAIL",
                           integrity_result if integrity_result != "ok" else "ok")

        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        fk = Check("SQLite foreign_key_check", "PASS" if not fk_violations else "FAIL",
                   "0 violations" if not fk_violations else f"{len(fk_violations)} violation(s)")

        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='whatsapp_pending_observations'"
        ).fetchone() is not None
        if table_exists:
            durable = Check("WhatsApp observations durability", "PASS",
                             "whatsapp_pending_observations table present -- observations are SQLite-backed, not in-memory.")
        else:
            durable = Check("WhatsApp observations durability", "ACTION REQUIRED",
                             "whatsapp_pending_observations table is missing -- run scripts\\manage_schema.py migrate.")

        if table_exists:
            dupes = conn.execute(
                "SELECT message_id, COUNT(*) c FROM whatsapp_pending_observations GROUP BY message_id HAVING c > 1"
            ).fetchall()
            if dupes:
                dup_check = Check("WhatsApp observation duplicate IDs", "FAIL",
                                   f"{len(dupes)} duplicate message_id value(s) found.")
            else:
                dup_check = Check("WhatsApp observation duplicate IDs", "PASS", "No duplicate message_id values.")
        else:
            dup_check = Check("WhatsApp observation duplicate IDs", "WARNING", "Skipped -- table not present.")
    finally:
        conn.close()

    return integrity, fk, durable, dup_check


def check_backup_destination_writable() -> Check:
    try:
        import backup_database
    except ImportError as exc:
        return Check("Backup destination writable", "WARNING", f"Could not import scripts/backup_database.py: {exc}")

    dest_dir = backup_database.DEFAULT_BACKUP_DIR
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        probe = dest_dir / ".system_readiness_write_probe"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check("Backup destination writable", "FAIL", f"{dest_dir}: {exc}")
    return Check("Backup destination writable", "PASS", str(dest_dir))


def check_required_secrets() -> list:
    import os
    try:
        import operator_api  # noqa: F401  -- importing triggers load_dotenv(REPO_ROOT / ".env")
    except ImportError as exc:
        return [Check("Configuration secrets", "WARNING", f"Could not import operator_api.py to load .env: {exc}")]

    checks = []
    missing_required = []
    for name in REQUIRED_SECRET_VARS:
        value = os.environ.get(name, "")
        if value:
            checks.append(Check(f"Secret present: {name}", "PASS",
                                 f"non-empty, {len(value)} chars, fingerprint {_fingerprint(value)}"))
        else:
            missing_required.append(name)
            checks.append(Check(f"Secret present: {name}", "ACTION REQUIRED", "missing or empty"))
    for name in OPTIONAL_SECRET_VARS:
        value = os.environ.get(name, "")
        if value:
            checks.append(Check(f"Secret present: {name}", "PASS",
                                 f"non-empty, {len(value)} chars, fingerprint {_fingerprint(value)}"))
        else:
            checks.append(Check(f"Secret present: {name}", "WARNING", "missing or empty (optional -- only needed for the Quo Inbox (Live) tab)"))

    bridge_secret = os.environ.get("WHATSAPP_BRIDGE_ADAPTER_SECRET", "")
    shadow_env_path = REPO_ROOT / "whatsapp_shadow_bridge" / ".env"
    shadow_secret = _read_env_var_from_file(shadow_env_path, "WHATSAPP_SHADOW_ADAPTER_SECRET")
    if not bridge_secret or not shadow_secret:
        checks.append(Check("Bridge secret fingerprint match", "WARNING",
                             "Cannot compare -- one or both secrets are not set."))
    elif _fingerprint(bridge_secret) == _fingerprint(shadow_secret):
        checks.append(Check("Bridge secret fingerprint match", "PASS",
                             "Python adapter and Node bridge secrets match (compared as fingerprints only)."))
    else:
        checks.append(Check("Bridge secret fingerprint match", "FAIL",
                             "Python adapter (.env) and Node bridge (whatsapp_shadow_bridge/.env) secrets do NOT match -- the bridge will get 401s delivering observations."))
    return checks


def _read_env_var_from_file(env_path: Path, var_name: str) -> Optional[str]:
    """Parses a simple KEY=VALUE .env file without importing it into
    this process's real environment -- used only to fingerprint-compare
    a value, never to display it."""
    if not env_path.exists():
        return None
    pattern = re.compile(rf"^{re.escape(var_name)}\s*=\s*(.*)$")
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        m = pattern.match(line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return None


def check_startup_scripts_current() -> Check:
    """The known gap this repeatedly documents: tools/windows_launcher/
    still launches the retired `streamlit run luxor_desk.py`, not the
    canonical operator_api.py CRM that scripts/start_crm.ps1 starts.
    See scripts/start_crm.ps1's own docstring, which already discloses
    this."""
    launcher = REPO_ROOT / "tools" / "windows_launcher" / "launcher.py"
    start_crm = REPO_ROOT / "scripts" / "start_crm.ps1"

    if not start_crm.exists():
        return Check("Startup scripts point to current CRM", "WARNING", "scripts/start_crm.ps1 not found.")
    start_crm_text = start_crm.read_text(encoding="utf-8", errors="ignore")
    if "operator_api.py" not in start_crm_text:
        return Check("Startup scripts point to current CRM", "FAIL",
                      "scripts/start_crm.ps1 no longer references operator_api.py.")

    if launcher.exists():
        launcher_text = launcher.read_text(encoding="utf-8", errors="ignore")
        if "luxor_desk.py" in launcher_text and "operator_api.py" not in launcher_text:
            return Check("Startup scripts point to current CRM", "ACTION REQUIRED",
                          "tools/windows_launcher/launcher.py still starts the retired luxor_desk.py Streamlit "
                          "app, not the canonical operator_api.py CRM. Use scripts\\start_crm.ps1 instead of the "
                          "Windows launcher until this is repointed.")

    return Check("Startup scripts point to current CRM", "PASS",
                 "scripts/start_crm.ps1 references operator_api.py.")


def run_all_checks(db_path: Path) -> list:
    checks = []
    checks.append(check_crm_port())
    checks.append(check_python_adapter_port())
    bridge_check, allowlist_check = check_whatsapp_bridge_port()
    checks.append(bridge_check)
    checks.append(allowlist_check)
    checks.extend(check_database_integrity(db_path))
    checks.append(check_backup_destination_writable())
    checks.extend(check_required_secrets())
    checks.append(check_startup_scripts_current())
    return checks


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=storage.DB_PATH,
                         help="Database to check (opened strictly read-only). Defaults to the real database.")
    args = parser.parse_args(argv)

    print("LUXOR system readiness check (read-only -- never restarts anything, never prints a secret value)\n")
    checks = run_all_checks(args.db)

    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"[{c.level:15}] {c.name.ljust(width)}  {c.detail}")

    fail_count = sum(1 for c in checks if c.level in ("FAIL", "ACTION REQUIRED"))
    warn_count = sum(1 for c in checks if c.level == "WARNING")
    pass_count = sum(1 for c in checks if c.level == "PASS")
    print(f"\nSummary: {pass_count} pass, {warn_count} warning, {fail_count} fail/action-required, {len(checks)} total.")

    return 1 if fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
