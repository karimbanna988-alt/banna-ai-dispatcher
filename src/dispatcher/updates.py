"""Maps a technician's confirmed status text to the standard short reply
sent back to a company, per docs/workflow.md section 8. Never invents a
status -- unrecognized text comes back unconfirmed rather than guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class StandardReply:
    reply: Optional[str]
    confirmed: bool
    raw_input: str
    reason: str


def _going(_m: "re.Match") -> str:
    return "Going"


def _eta(m: "re.Match") -> str:
    return f"Tech ETA {m.group('minutes')} min"


def _no_answer(_m: "re.Match") -> str:
    return "No answer"


def _on_phone(_m: "re.Match") -> str:
    return "Tech on phone"


def _pass(_m: "re.Match") -> str:
    return "Pass"


def _called(m: "re.Match") -> str:
    return "Tech calling now" if m.group("now") else "Tech called"


def _not_yet(_m: "re.Match") -> str:
    return "Not yet"


_STATUS_PATTERNS = [
    (re.compile(r"^going$", re.IGNORECASE), _going),
    (re.compile(r"^eta\s*(?P<minutes>\d+)\s*(?:min(?:ute)?s?)?$", re.IGNORECASE), _eta),
    (re.compile(r"^no answer$", re.IGNORECASE), _no_answer),
    (re.compile(r"^on\s*phone$", re.IGNORECASE), _on_phone),
    (re.compile(r"^pass$", re.IGNORECASE), _pass),
    (re.compile(r"^(?:tech\s+)?call(?:ed|ing)(?P<now>\s+now)?$", re.IGNORECASE), _called),
    (re.compile(r"^not\s+yet$", re.IGNORECASE), _not_yet),
]


def map_status_to_reply(raw_text: str) -> StandardReply:
    text = raw_text.strip()
    for pattern, build in _STATUS_PATTERNS:
        m = pattern.match(text)
        if m:
            return StandardReply(
                reply=build(m), confirmed=True, raw_input=raw_text,
                reason="matched a known technician status phrase",
            )
    return StandardReply(
        reply=None, confirmed=False, raw_input=raw_text,
        reason="technician status text not recognized -- ask the dispatcher to confirm before replying",
    )


_UPDATE_TYPE_REPLIES = {
    "Going": "Going",
    "No Answer": "No answer",
    "No Show": "No show",
    "Not Available": "Technician not available",
    "Customer Cancelled": "Customer cancelled",
    "Completed": "Completed",
}


def reply_for_update_type(update_type: str, eta_minutes: Optional[int] = None) -> StandardReply:
    """Deterministic company-facing reply for one of the dispatcher-
    selected technician update types (Active Jobs' RECORD TECH UPDATE --
    the type is always explicitly picked by the dispatcher after reading
    the technician's real reply, never inferred from free text). "Custom"
    always comes back unconfirmed: composing a safe company-facing
    message for an update that doesn't fit a known type is a human's job,
    not this function's -- matching map_status_to_reply()'s own "never
    invents a status" rule.
    """
    if update_type == "ETA":
        if not eta_minutes:
            return StandardReply(
                reply=None, confirmed=False, raw_input=update_type,
                reason="ETA update requires a minutes value -- never invented",
            )
        return StandardReply(
            reply=f"Tech ETA {eta_minutes} min", confirmed=True, raw_input=update_type,
            reason="dispatcher-selected ETA update",
        )
    if update_type in _UPDATE_TYPE_REPLIES:
        return StandardReply(
            reply=_UPDATE_TYPE_REPLIES[update_type], confirmed=True, raw_input=update_type,
            reason="dispatcher-selected technician update type",
        )
    return StandardReply(
        reply=None, confirmed=False, raw_input=update_type,
        reason="custom/unrecognized update type -- requires human review before a company reply can be sent",
    )


def answer_from_latest_status(latest: Optional[StandardReply]) -> StandardReply:
    """Used for company questions like "Any update?" or "Did anyone call?" --
    "Did anyone call?" means whether OUR technician called the customer, and
    both are answered only from the latest confirmed technician status, never
    invented.
    """
    if latest is None or not latest.confirmed:
        return StandardReply(
            reply=None, confirmed=False, raw_input="",
            reason="no confirmed technician status available -- ask the dispatcher to check with the technician",
        )
    return latest
