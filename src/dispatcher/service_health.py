"""Are Banna's three services actually running?

The CRM cannot answer this by asking them. operator_api is forbidden
from making outbound HTTP -- a rule worth keeping, since a dashboard
that blocks on a dead socket is worse than one that says nothing. And
silence in the data is not an answer either: at 5am in New York a quiet
board and a dead bridge look identical, which is exactly the confusion
this module exists to end.

So the watchdog that already supervises those services records what it
sees (ops/service_status.json), and this reads it.

The file's OWN age is the most important signal it carries. A stale
file means the watchdog itself stopped, so every flag inside it is
merely the last thing that was true -- never reported as current.

Pure and defensive: no exception escapes, because a health check that
can break the page it is meant to protect is not a health check.
"""

import json
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Optional

STATUS_FILE = pathlib.Path(__file__).resolve().parents[2] / "ops" / "service_status.json"

# The watchdog runs every 5 minutes. Two missed cycles is a real fault,
# not a slow disk.
STALE_AFTER = timedelta(minutes=12)

SERVICE_LABELS = {
    "api": "CRM server",
    "bridge": "WhatsApp capture",
    "quo": "Quo polling",
}


def _parse(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


def read_service_health(now_utc: Optional[datetime] = None, path: Optional[pathlib.Path] = None) -> dict:
    """What the supervisor last saw, and whether that is still current."""
    now = now_utc or datetime.now(timezone.utc)
    target = path or STATUS_FILE

    try:
        raw = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        # No file at all means the watchdog has never run here. That is
        # unknown, not healthy, and definitely not "down" -- claiming a
        # service is down when nobody looked would be its own false alarm.
        return {"known": False, "watchdog_alive": False, "checked_at": None,
                "down": [], "reason": "no supervisor report found"}

    checked_at = _parse(raw.get("checked_at"))
    age = (now - checked_at) if checked_at else None
    watchdog_alive = bool(age is not None and age <= STALE_AFTER)

    down = [SERVICE_LABELS[key] for key in ("api", "bridge", "quo")
            if key in raw and not raw.get(key)]

    if not watchdog_alive:
        reason = ("the supervisor has not reported in "
                  f"{int(age.total_seconds() // 60)} minutes" if age else
                  "the supervisor's last report has no usable timestamp")
    elif down:
        reason = ", ".join(down) + (" is not running" if len(down) == 1 else " are not running")
    else:
        reason = None

    return {
        "known": True,
        "watchdog_alive": watchdog_alive,
        "checked_at": checked_at.isoformat() if checked_at else None,
        "age_seconds": int(age.total_seconds()) if age else None,
        "down": down,
        # The single question the banner asks.
        "healthy": bool(watchdog_alive and not down),
        "reason": reason,
    }
