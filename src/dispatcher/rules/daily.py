"""Parses the dispatcher's freeform shift-start message into structured,
temporary rules that override the permanent service-area mapping for the
day. Never guesses: any line that doesn't match a known pattern is kept in
`unparsed_lines` and surfaced for the dispatcher to confirm rather than
silently dropped or misinterpreted. See docs/workflow.md section 10.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

DEFAULT_SHIFT_HOURS = 12

_HEADER_RE = re.compile(r"^today:?$", re.IGNORECASE)
_BULLET_RE = re.compile(r"^[-*•]\s*")

_OPEN_RE = re.compile(r"^(?P<area>.+?)\s+(?:is\s+)?open$", re.IGNORECASE)
_CLOSED_RE = re.compile(r"^(?P<area>.+?)\s+(?:is\s+)?closed$", re.IGNORECASE)
_TECH_STATUS_RE = re.compile(
    r"^(?P<area>.+?)\s+(?:technician|tech)\s+(?:is\s+)?(?P<status>available|busy|off)$",
    re.IGNORECASE,
)
_DO_NOT_ACCEPT_RE = re.compile(r"^do not accept\s+(?P<item>.+?)(?:\s+today)?$", re.IGNORECASE)


@dataclass
class DailyRules:
    raw_text: str
    open_areas: List[str] = field(default_factory=list)
    closed_areas: List[str] = field(default_factory=list)
    technician_status: Dict[str, str] = field(default_factory=dict)
    temporary_restrictions: List[str] = field(default_factory=list)
    unparsed_lines: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime = field(default_factory=lambda: datetime.now() + timedelta(hours=DEFAULT_SHIFT_HOURS))
    extended: bool = False

    @property
    def needs_confirmation(self) -> bool:
        return bool(self.unparsed_lines)

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        return (now or datetime.now()) >= self.expires_at

    def extend(self, new_expiry: datetime) -> None:
        self.expires_at = new_expiry
        self.extended = True

    def summary(self) -> str:
        lines = ["Today's temporary rules:"]
        lines.append(f"  Open areas: {', '.join(self.open_areas) or '(none stated)'}")
        lines.append(f"  Closed areas: {', '.join(self.closed_areas) or '(none stated)'}")
        if self.technician_status:
            tech = ", ".join(f"{area}: {status}" for area, status in self.technician_status.items())
            lines.append(f"  Technician status: {tech}")
        else:
            lines.append("  Technician status: (none stated)")
        lines.append(f"  Temporary restrictions: {', '.join(self.temporary_restrictions) or '(none)'}")
        lines.append(f"  Expires: {self.expires_at.isoformat()}")
        if self.unparsed_lines:
            lines.append("  COULD NOT PARSE (needs your confirmation):")
            for line in self.unparsed_lines:
                lines.append(f"    - {line}")
        return "\n".join(lines)


def parse_daily_rules(raw_text: str, shift_hours: int = DEFAULT_SHIFT_HOURS, now: Optional[datetime] = None) -> DailyRules:
    created_at = now or datetime.now()
    rules = DailyRules(raw_text=raw_text, created_at=created_at, expires_at=created_at + timedelta(hours=shift_hours))

    for raw_line in raw_text.splitlines():
        line = _BULLET_RE.sub("", raw_line.strip()).strip()
        if not line or _HEADER_RE.match(line):
            continue

        m = _TECH_STATUS_RE.match(line)
        if m:
            rules.technician_status[m.group("area").strip()] = m.group("status").lower()
            continue

        m = _OPEN_RE.match(line)
        if m:
            rules.open_areas.append(m.group("area").strip())
            continue

        m = _CLOSED_RE.match(line)
        if m:
            rules.closed_areas.append(m.group("area").strip())
            continue

        m = _DO_NOT_ACCEPT_RE.match(line)
        if m:
            rules.temporary_restrictions.append(m.group("item").strip())
            continue

        rules.unparsed_lines.append(line)

    return rules
