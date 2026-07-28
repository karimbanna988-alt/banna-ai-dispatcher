"""Controlled, disabled-by-default Quo outbound boundary.

Completely separate from quo_client.py (ingestion, strictly GET-only) --
this is the only module in the codebase with any concept of sending to
Quo, and even here, no HTTP send implementation exists yet. Every code
path through propose_send() either returns before attempting a network
call or (for "live" mode) explicitly refuses, because the real send call
is intentionally not implemented in this build -- see the module-level
NotImplementedError in _send_live. QUO_OUTBOUND_MODE defaults to
"disabled"; setting it to "live" does not enable sending, because
sending was never wired up in the first place.

Every attempt -- previewed, blocked, or duplicate -- is durably recorded
via storage.record_outbound_attempt() BEFORE any send would be attempted,
so the audit trail covers every attempt, not only successful ones.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from . import storage

OUTBOUND_MODE_ENV_VAR = "QUO_OUTBOUND_MODE"
# "on"/"1"/"true" unconditionally blocks every send attempt, regardless of
# QUO_OUTBOUND_MODE -- a single environment variable a human can flip
# without needing to know which mode was previously configured.
KILL_SWITCH_ENV_VAR = "QUO_OUTBOUND_KILL_SWITCH"
# Comma-separated Source values (see dispatcher.models.Source) permitted
# to send at all. Empty (the default) means no source is allowed, even in
# preview mode -- an explicit opt-in per source, not an opt-out.
SOURCE_ALLOWLIST_ENV_VAR = "QUO_OUTBOUND_SOURCE_ALLOWLIST"
# Comma-separated Quo contact ids permitted as a send recipient while
# testing approval-mode wiring -- deliberately separate from the source
# allowlist so a real company contact can never be reached by test
# configuration alone.
TEST_RECIPIENT_ALLOWLIST_ENV_VAR = "QUO_OUTBOUND_TEST_RECIPIENTS"


class OutboundMode(str, Enum):
    DISABLED = "disabled"
    PREVIEW = "preview"
    APPROVAL = "approval"
    LIVE = "live"


class OutboundConfigError(Exception):
    pass


def current_mode() -> OutboundMode:
    raw = os.environ.get(OUTBOUND_MODE_ENV_VAR, OutboundMode.DISABLED.value).strip().lower()
    try:
        return OutboundMode(raw)
    except ValueError:
        # An unrecognized value is a configuration mistake, not an
        # intentional escalation -- fail to the safest mode rather than
        # guessing what was meant.
        return OutboundMode.DISABLED


def kill_switch_engaged() -> bool:
    return os.environ.get(KILL_SWITCH_ENV_VAR, "").strip().lower() in ("on", "1", "true", "yes")


def _allowlisted_sources() -> set:
    raw = os.environ.get(SOURCE_ALLOWLIST_ENV_VAR, "")
    return {s.strip() for s in raw.split(",") if s.strip()}


def _test_recipient_allowlist() -> set:
    raw = os.environ.get(TEST_RECIPIENT_ALLOWLIST_ENV_VAR, "")
    return {s.strip() for s in raw.split(",") if s.strip()}


@dataclass(frozen=True)
class OutboundRequest:
    source: str
    job_id: Optional[int]
    quo_contact_id: str
    text: str
    idempotency_key: str


@dataclass(frozen=True)
class OutboundResult:
    outcome: str  # "previewed" | "blocked" | "duplicate"
    reason: str
    request: OutboundRequest


def generate_idempotency_key(source: str, job_id: Optional[int], quo_contact_id: str, text: str) -> str:
    """Deterministic: the same (source, job, recipient, text) always
    produces the same key, so an accidental double-submit of the exact
    same proposed message is caught by storage's UNIQUE constraint even
    before is_outbound_duplicate() is checked."""
    basis = f"{source}|{job_id}|{quo_contact_id}|{text}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def build_request(*, source: str, job_id: Optional[int], quo_contact_id: str, text: str) -> OutboundRequest:
    key = generate_idempotency_key(source, job_id, quo_contact_id, text)
    return OutboundRequest(source=source, job_id=job_id, quo_contact_id=quo_contact_id, text=text, idempotency_key=key)


def _send_live(conn: sqlite3.Connection, request: OutboundRequest) -> OutboundResult:
    """Deliberately unimplemented. Reaching this function means every
    upstream guard (kill switch, allowlist, mode) already passed for
    "live" mode -- and it still refuses, because no real Quo send call
    has been written in this codebase. Implementing this requires a
    separate, explicitly approved change, never a side effect of any
    other task."""
    raise NotImplementedError(
        "Live Quo sending is not implemented in this build. QUO_OUTBOUND_MODE=live changes "
        "configuration only -- it does not enable an unwritten capability."
    )


def propose_send(
    conn: sqlite3.Connection, request: OutboundRequest, *, approved_by: Optional[str] = None,
) -> OutboundResult:
    """The single entry point for any outbound attempt. Every branch that
    does not end in an actual send is a `return` -- there is no
    fallthrough path into _send_live()."""
    mode = current_mode()

    if storage.is_outbound_duplicate(conn, request.idempotency_key):
        result = OutboundResult("duplicate", "An identical request was already recorded.", request)
        return result

    if kill_switch_engaged():
        outcome, reason = "blocked", "Global outbound kill switch is engaged."
    elif mode == OutboundMode.DISABLED:
        outcome, reason = "blocked", "Outbound is disabled (QUO_OUTBOUND_MODE=disabled)."
    elif request.source not in _allowlisted_sources():
        outcome, reason = "blocked", f"Source {request.source!r} is not on the outbound allowlist."
    elif mode == OutboundMode.PREVIEW:
        outcome, reason = "previewed", "Preview mode -- no network call made."
    elif mode == OutboundMode.APPROVAL:
        if not approved_by:
            outcome, reason = "blocked", "Approval mode requires an explicit approved_by value."
        elif request.quo_contact_id not in _test_recipient_allowlist():
            outcome, reason = "blocked", "Recipient is not on the test-recipient allowlist."
        else:
            outcome, reason = "blocked", "Approval-mode sending is not implemented in this build."
    else:
        outcome, reason = "blocked", "Live outbound is not implemented in this build."

    # Recorded regardless of outcome -- durable before any send would be
    # attempted, matching the ingestion side's content-before-processed
    # ordering.
    storage.record_outbound_attempt(
        conn, idempotency_key=request.idempotency_key, source=request.source, job_id=request.job_id,
        quo_contact_id=request.quo_contact_id, proposed_text=request.text, mode=mode.value,
        outcome=outcome, reason=reason, approved_by=approved_by,
    )
    return OutboundResult(outcome, reason, request)
