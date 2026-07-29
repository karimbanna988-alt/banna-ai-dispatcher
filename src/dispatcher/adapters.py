"""Clean interfaces for future external actions -- Quo outbound sending
and WhatsApp handoff -- kept disabled/manual in this build.

Neither adapter here can cause a real external side effect. The Quo
adapter wraps the already-existing, already disabled-by-default
outbound.py boundary (no new send capability is added -- see
outbound.py's own module docstring: `_send_live` remains
`NotImplementedError`). The WhatsApp adapter never sends anything at
all -- WhatsApp has no supported programmatic send path in this
codebase and none is added here; it only prepares text, reports the
configured destination, and records an explicit human confirmation
after the dispatcher has manually sent the message themselves.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional, Protocol

from . import outbound, storage


@dataclass(frozen=True)
class OutboundAttemptResult:
    """Mirrors outbound.OutboundResult's shape (outcome/reason) plus the
    adapter's own name, so a caller logging/displaying this never needs
    to import outbound.py directly."""

    outcome: str  # "previewed" | "blocked" | "duplicate"
    reason: str
    adapter_name: str


class QuoOutboundAdapter(Protocol):
    """Future seam for a real Quo send capability. No implementation of
    this protocol in this codebase is permitted to perform a network
    write -- see DisabledQuoOutboundAdapter, the only implementation
    that currently exists."""

    def send_acknowledgment(
        self, conn: sqlite3.Connection, *, source: str, job_id: Optional[int],
        quo_contact_id: str, text: str, approved_by: Optional[str] = None,
    ) -> OutboundAttemptResult: ...

    def is_enabled(self) -> bool: ...


class DisabledQuoOutboundAdapter:
    """The only QuoOutboundAdapter implementation in this build. Every
    call routes through outbound.propose_send() -- the same disabled-by-
    default boundary the rest of the codebase already uses -- so this
    adapter adds no new way to reach Quo; it only gives that existing
    boundary a stable, swappable interface for the application service
    to depend on. `is_enabled()` reports the real current mode; it is
    never assumed True."""

    name = "DisabledQuoOutboundAdapter"

    def is_enabled(self) -> bool:
        return outbound.current_mode() != outbound.OutboundMode.DISABLED

    def send_acknowledgment(
        self, conn: sqlite3.Connection, *, source: str, job_id: Optional[int],
        quo_contact_id: str, text: str, approved_by: Optional[str] = None,
    ) -> OutboundAttemptResult:
        request = outbound.build_request(source=source, job_id=job_id, quo_contact_id=quo_contact_id, text=text)
        result = outbound.propose_send(conn, request, approved_by=approved_by)
        return OutboundAttemptResult(outcome=result.outcome, reason=result.reason, adapter_name=self.name)


@dataclass(frozen=True)
class WhatsAppDispatchInstruction:
    """A safe, manual-only handoff instruction -- never a delivery
    claim. `destination` is the technician's own opaque dispatch_group
    string (see storage.assign_technician()'s docstring); None means no
    destination is configured, and the caller must never invent one."""

    text: str
    destination: Optional[str]
    destination_configured: bool
    instruction: str = "Copy this text and send it manually in WhatsApp to the destination shown."


class WhatsAppHandoffAdapter(Protocol):
    def prepare(self, text: str, destination: Optional[str]) -> WhatsAppDispatchInstruction: ...

    def confirm_sent(self, conn: sqlite3.Connection, *, job_id: int, technician_id: Optional[int]) -> None: ...


class ManualWhatsAppHandoffAdapter:
    """The only WhatsAppHandoffAdapter implementation in this build, and
    the only one this codebase will ever need for WhatsApp specifically:
    there is no supported WhatsApp Business API integration and no
    browser/unofficial automation anywhere in this project. `prepare()`
    only formats a manual-open instruction; it never opens a browser,
    never calls any WhatsApp API, and never claims delivery.
    `confirm_sent()` records the dispatcher's own explicit confirmation
    via the existing storage.record_whatsapp_handoff() -- the same
    function the current UI already calls."""

    name = "ManualWhatsAppHandoffAdapter"

    def prepare(self, text: str, destination: Optional[str]) -> WhatsAppDispatchInstruction:
        return WhatsAppDispatchInstruction(
            text=text, destination=destination, destination_configured=destination is not None,
        )

    def confirm_sent(self, conn: sqlite3.Connection, *, job_id: int, technician_id: Optional[int]) -> None:
        storage.record_whatsapp_handoff(conn, job_id, technician_id)
