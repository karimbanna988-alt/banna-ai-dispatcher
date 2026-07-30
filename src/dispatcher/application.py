"""Application service layer -- the one place UI actions (and, in the
future, CLI/API adapters) turn into business transactions.

DispatcherService orchestrates the existing deterministic authorities
-- policy.py, source_adapters.py, closing.py, updates.py, storage.py --
and never replaces any of them. It owns no SQL of its own (every
persisted fact goes through storage.py) and no status-derivation logic
of its own (storage.derive_job_status() remains the single authority --
see tests/test_application_service.py::test_no_competing_status_derivation
for a structural guard proving this file contains no such logic).

Every state-changing command:
  1. loads current persisted state (via storage.get_job_by_id() or
     equivalent -- never a reconstructed ExtractedJob);
  2. validates required input, returning ActionResult.failure() with
     ValidationIssue entries rather than raising for ordinary bad input;
  3. computes a deterministic idempotency key and checks it first, so a
     Streamlit rerun, double-click, or poller retry that resubmits the
     same logical action returns idempotent_replay=True instead of
     creating a duplicate audit event;
  4. delegates the actual decision to the one relevant deterministic
     authority (policy.get_policy(), closing.render_closing_from_persisted_fields(),
     updates.reply_for_update_type(), storage.derive_job_status());
  5. persists through storage.py's existing transactional methods;
  6. returns a structured ActionResult -- never a raw dict, never
     nothing.

Nothing here ever sends a real Quo or WhatsApp message: `quo_adapter`
defaults to adapters.DisabledQuoOutboundAdapter (itself routed through
the already disabled-by-default outbound.py boundary) and
`whatsapp_adapter` defaults to adapters.ManualWhatsAppHandoffAdapter
(prepares text and records a human's own confirmation; never sends
anything itself).

This module has no import-time dependency on Streamlit, and every
write-side command works from a plain sqlite3.Connection -- safe to
call from a Streamlit page, a test, a CLI tool, or a future API
adapter. The read-side convenience method get_queue() is the one
exception (see its own docstring): it lazily imports the existing
repo-root read model rather than reimplementing its display-formatting
logic a second time.
"""

from __future__ import annotations

import dataclasses
import hashlib
import sqlite3
from typing import List, Optional, Protocol

from . import closing as closing_module
from . import commercial, storage, updates
from .action_names import ActionName, STATUS_CANCELLED, STATUS_CLOSED
from .adapters import DisabledQuoOutboundAdapter, ManualWhatsAppHandoffAdapter, QuoOutboundAdapter, WhatsAppHandoffAdapter
from .app_models import (
    ActionResult,
    ClosingCommand,
    HumanReviewReason,
    TechnicianUpdateCommand,
    ValidationIssue,
)
from .closing import ClosingInput
from .shadow import ComparisonLabel, ShadowChannel, ShadowEventType, compare_shadow_to_actual
from .storage import InvalidJobActionError


def _fingerprint(text: str) -> str:
    """Same normalization luxor_desk_read_model._content_fingerprint()
    already uses -- whitespace/case-insensitive, so two submissions that
    differ only in incidental formatting still collapse to the same
    idempotency key, matching the same tolerance the New Jobs dedup
    logic already applies to inbound messages."""
    normalized = " ".join((text or "").split()).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _commercial_update_to_dict(cu: commercial.CommercialUpdate) -> dict:
    """JSON-safe snapshot of a CommercialUpdate for shadow_events/
    shadow_episodes payloads -- enum members become their .value."""
    return {
        "raw_technician_text": cu.raw_technician_text,
        "quote_type": cu.quote_type.value,
        "quote_amount": cu.quote_amount,
        "quote_amount_text": cu.quote_amount_text,
        "quote_range_low": cu.quote_range_low,
        "quote_range_high": cu.quote_range_high,
        "currency": cu.currency,
        "customer_response": cu.customer_response.value,
        "customer_response_text": cu.customer_response_text,
        "follow_up_required": cu.follow_up_required,
        "follow_up_time_text": cu.follow_up_time_text,
        "negotiation_state": cu.negotiation_state.value,
        "confidence": cu.confidence,
        "validation_issues": cu.validation_issues,
        "human_review_required": cu.human_review_required,
    }


def compute_idempotency_key(identity, action: str, fingerprint_text: str = "") -> str:
    """Deterministic: the same (identity, action, fingerprint_text)
    always produces the same key. `identity` is a job_id (int) for
    already-committed jobs, or a quo_pending_messages.message_id (str)
    for pending-message-level actions (acknowledge, confirm dispatch) --
    either way, always something that already uniquely identifies the
    thing being acted on, never invented."""
    basis = f"{identity}|{action}|{_fingerprint(fingerprint_text)}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


class QueueReader(Protocol):
    """Seam for the one read this module cannot serve on its own
    without either duplicating luxor_desk_read_model.py's dedup/
    canonicalization logic or importing that repo-root, UI-adjacent
    module directly (a prohibited dependency direction -- application
    code must never import a luxor_desk_* module; see
    docs/ARCHITECTURE_FOUNDATION.md and
    tests/test_dependency_boundaries.py). A concrete implementation
    lives at the repo root (luxor_desk_read_model.LuxorDeskQueueReader)
    and is constructed by the UI layer, which already depends on that
    module -- application.py itself never imports it, at module scope
    or inside any method."""

    def read_queue(self, conn: sqlite3.Connection, kind: str) -> list: ...


class DispatcherService:
    """One instance per request/page-render, wrapping one
    sqlite3.Connection -- mirrors how every luxor_desk_pages/*.py file
    already does `conn = storage.connect()` once per render."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        whatsapp_adapter: Optional[WhatsAppHandoffAdapter] = None,
        quo_adapter: Optional[QuoOutboundAdapter] = None,
        queue_reader: Optional[QueueReader] = None,
    ) -> None:
        self.conn = conn
        self.whatsapp_adapter = whatsapp_adapter or ManualWhatsAppHandoffAdapter()
        self.quo_adapter = quo_adapter or DisabledQuoOutboundAdapter()
        self.queue_reader = queue_reader

    # --------------------------------------------- New Job / dispatch -----
    def acknowledge_job_manually(self, message_id: str) -> ActionResult:
        """The manual "MARK K SENT MANUALLY" confirmation. Already
        idempotent by construction at the storage layer
        (dispatch_tracking's COALESCE-based "set once" pattern) -- this
        wrapper additionally reports whether it was a replay."""
        tracking = storage.get_dispatch_tracking(self.conn, message_id)
        was_already = tracking["acknowledged_at"] is not None
        storage.mark_acknowledged(self.conn, message_id)
        return ActionResult(
            success=True, action="acknowledge_job_manually", idempotent_replay=was_already,
            user_message="Already acknowledged." if was_already else "Acknowledgment recorded.",
        )

    def attempt_quo_acknowledgment(
        self, message_id: str, *, source: str, job_id: Optional[int], quo_contact_id: str, text: str = "K",
    ) -> ActionResult:
        """The APPROVAL/LIVE-mode "Send K" path -- always routes through
        self.quo_adapter (DisabledQuoOutboundAdapter by default), which
        itself routes through the existing, disabled-by-default
        outbound.propose_send(). In this build's real configuration
        (QUO_OUTBOUND_MODE unset/disabled) this always ends up blocked;
        never a real send."""
        storage.mark_acknowledged(self.conn, message_id)
        result = self.quo_adapter.send_acknowledgment(
            self.conn, source=source, job_id=job_id, quo_contact_id=quo_contact_id, text=text,
        )
        return ActionResult(
            success=True, action="attempt_quo_acknowledgment", job_id=job_id,
            user_message=f"Outbound: {result.outcome} — {result.reason}",
        )

    def prepare_technician_dispatch(self, message_id: str) -> ActionResult:
        """The "DISPATCH IN WHATSAPP" click -- marks the Luxor message
        copied and WhatsApp opened. Does not format the message itself
        (the caller already has the formatted text from the existing
        cached pipeline preview); this command only records the two
        dispatch_tracking milestones, both already idempotent by
        construction (COALESCE "set once")."""
        tracking = storage.get_dispatch_tracking(self.conn, message_id)
        was_already = tracking["copied_at"] is not None and tracking["whatsapp_opened_at"] is not None
        storage.mark_copied(self.conn, message_id)
        storage.mark_whatsapp_opened(self.conn, message_id)
        return ActionResult(
            success=True, action="prepare_technician_dispatch", idempotent_replay=was_already,
            user_message="Ready to send in WhatsApp.",
        )

    def confirm_whatsapp_dispatch(
        self, message_id: str, job, classify_result, luxor_message: str, technician_id: Optional[int] = None,
    ) -> ActionResult:
        """The "CONFIRM DISPATCHED" click -- the one action that
        actually commits a job (creates/updates the `jobs` row via
        storage.save_audit_entry()) and records the WhatsApp handoff
        confirmation via self.whatsapp_adapter (never claims delivery
        itself; only records that the dispatcher confirmed it). Keyed by
        `message_id` -- this exact pending message can only ever be
        confirmed-dispatched once."""
        idempotency_key = compute_idempotency_key(message_id, "confirm_whatsapp_dispatch")
        existing = storage.get_event_by_idempotency_key(self.conn, idempotency_key)
        if existing is not None:
            return ActionResult(
                success=True, action="confirm_whatsapp_dispatch", job_id=existing["job_id"],
                audit_event_id=existing["id"], idempotent_replay=True, user_message="Already recorded as dispatched.",
            )
        job_id = storage.save_audit_entry(
            self.conn, job, classify_result, final_action=ActionName.APPROVE.value,
            luxor_message=luxor_message, channel="manual", idempotency_key=idempotency_key,
        )
        storage.mark_quo_pending_message_actioned(self.conn, message_id)
        storage.mark_dispatched_manually(self.conn, message_id)
        self.whatsapp_adapter.confirm_sent(self.conn, job_id=job_id, technician_id=technician_id)
        updated = storage.get_job_by_id(self.conn, job_id)
        return ActionResult(
            success=True, action="confirm_whatsapp_dispatch", job_id=job_id,
            current_status=updated["status"] if updated else None, user_message="Marked dispatched.",
        )

    def reject_pending_message(
        self, message_id: str, job, classify_result, luxor_message: Optional[str] = None, reason: Optional[str] = None,
    ) -> ActionResult:
        """The New Jobs "Handle Manually" / "Pass / Cancel Job" secondary
        actions -- for a *pending message* that never became a real
        dispatched job. Distinct from pass_or_cancel_job(), which acts
        on an already-committed job_id."""
        final_action = ActionName.REJECT.value if reason else ActionName.HANDLE_MANUALLY.value
        idempotency_key = compute_idempotency_key(message_id, f"reject_pending_message:{final_action}", reason or "")
        existing = storage.get_event_by_idempotency_key(self.conn, idempotency_key)
        if existing is not None:
            return ActionResult(
                success=True, action="reject_pending_message", job_id=existing["job_id"],
                audit_event_id=existing["id"], idempotent_replay=True, user_message="Already recorded.",
            )
        job_id = storage.save_audit_entry(
            self.conn, job, classify_result, final_action=final_action, luxor_message=luxor_message,
            dispatcher_edits=(f"Reason: {reason.strip()}" if reason else None),
            channel="manual", idempotency_key=idempotency_key,
        )
        storage.mark_quo_pending_message_actioned(self.conn, message_id)
        return ActionResult(success=True, action="reject_pending_message", job_id=job_id, user_message="Recorded.")

    # ------------------------------------------------------ Active Jobs ---
    def record_technician_update(self, command: TechnicianUpdateCommand) -> ActionResult:
        issues: List[ValidationIssue] = []
        if not command.raw_update or not command.raw_update.strip():
            issues.append(ValidationIssue(field="raw_update", problem="the technician's actual reply is required"))
        if command.update_type not in storage._TECHNICIAN_UPDATE_TYPES:
            issues.append(ValidationIssue(field="update_type", problem=f"unrecognized: {command.update_type!r}"))
        if command.update_type == "ETA" and not command.eta_minutes:
            issues.append(ValidationIssue(field="eta_minutes", problem="required for ETA updates -- never invented"))
        if issues:
            return ActionResult.failure("record_technician_update", job_id=command.job_id, issues=issues)

        idempotency_key = compute_idempotency_key(
            command.job_id, "record_technician_update",
            f"{command.update_type}|{command.eta_minutes}|{command.raw_update}",
        )
        was_replay = storage.get_event_by_idempotency_key(self.conn, idempotency_key) is not None
        try:
            job = storage.record_technician_update(
                self.conn, command.job_id, command.update_type, command.raw_update,
                eta_minutes=command.eta_minutes, idempotency_key=idempotency_key,
            )
        except InvalidJobActionError as exc:
            return ActionResult.failure("record_technician_update", job_id=command.job_id, message=str(exc))
        human_review = command.update_type == "Custom"
        return ActionResult(
            success=True, action="record_technician_update", job_id=command.job_id,
            current_status=job["status"], idempotent_replay=was_replay, human_review_required=human_review,
            human_review_reason=(
                HumanReviewReason(code="ambiguous_update", detail="Custom update -- no deterministic company reply available.")
                if human_review else None
            ),
            user_message="Technician update recorded.",
        )

    def prepare_company_update(
        self, job_id: int, update_type: str, eta_minutes: Optional[int] = None, manual_text: Optional[str] = None,
    ) -> ActionResult:
        """Computes the deterministic company-facing reply for a known
        update type via updates.reply_for_update_type() -- the one
        authority for this text. For "Custom" (or any type that comes
        back unconfirmed), the caller may supply `manual_text` -- a
        human-typed message, still recorded verbatim, never auto-
        generated."""
        reply = updates.reply_for_update_type(update_type, eta_minutes)
        prepared_text = reply.reply if reply.confirmed else manual_text
        if not prepared_text:
            return ActionResult(
                success=False, action="prepare_company_update", job_id=job_id, human_review_required=True,
                human_review_reason=HumanReviewReason(code="no_deterministic_reply", detail=reply.reason),
                user_message=reply.reason,
            )
        idempotency_key = compute_idempotency_key(job_id, "prepare_company_update", prepared_text)
        was_replay = storage.get_event_by_idempotency_key(self.conn, idempotency_key) is not None
        try:
            job = storage.mark_company_update_prepared(self.conn, job_id, prepared_text, idempotency_key=idempotency_key)
        except InvalidJobActionError as exc:
            return ActionResult.failure("prepare_company_update", job_id=job_id, message=str(exc))
        return ActionResult(
            success=True, action="prepare_company_update", job_id=job_id, current_status=job["status"],
            prepared_text=prepared_text, idempotent_replay=was_replay, user_message="Prepared.",
        )

    def confirm_company_update_sent(self, job_id: int, prepared_text: str) -> ActionResult:
        idempotency_key = compute_idempotency_key(job_id, "confirm_company_update_sent", prepared_text)
        was_replay = storage.get_event_by_idempotency_key(self.conn, idempotency_key) is not None
        try:
            job = storage.mark_company_update_sent_manually(self.conn, job_id, prepared_text, idempotency_key=idempotency_key)
        except InvalidJobActionError as exc:
            return ActionResult.failure("confirm_company_update_sent", job_id=job_id, message=str(exc))
        return ActionResult(
            success=True, action="confirm_company_update_sent", job_id=job_id, current_status=job["status"],
            idempotent_replay=was_replay, user_message="Recorded as sent.",
        )

    def pass_or_cancel_job(self, job_id: int, final_action: str, reason: str) -> ActionResult:
        if not reason or not reason.strip():
            return ActionResult.failure(
                "pass_or_cancel_job", job_id=job_id,
                issues=[ValidationIssue(field="reason", problem="required to pass/cancel a job")],
            )
        idempotency_key = compute_idempotency_key(job_id, f"pass_or_cancel_job:{final_action}", reason)
        was_replay = storage.get_event_by_idempotency_key(self.conn, idempotency_key) is not None
        try:
            job = storage.pass_or_cancel_job(self.conn, job_id, final_action, reason, idempotency_key=idempotency_key)
        except InvalidJobActionError as exc:
            return ActionResult.failure("pass_or_cancel_job", job_id=job_id, message=str(exc))
        return ActionResult(
            success=True, action="pass_or_cancel_job", job_id=job_id, current_status=job["status"],
            idempotent_replay=was_replay, user_message="Job passed/cancelled.",
        )

    # ---------------------------------------------------------- Closings --
    def record_closing(self, command: ClosingCommand) -> ActionResult:
        if not command.raw_closing or not command.raw_closing.strip():
            return ActionResult.failure(
                "record_closing", job_id=command.job_id,
                issues=[ValidationIssue(field="raw_closing", problem="the technician's actual closing text is required")],
            )
        idempotency_key = compute_idempotency_key(
            command.job_id, "record_closing",
            f"{command.raw_closing}|{command.final_amount}|{command.payment_method}|{command.parts}",
        )
        was_replay = storage.get_event_by_idempotency_key(self.conn, idempotency_key) is not None
        try:
            job = storage.record_job_closing(
                self.conn, command.job_id, command.raw_closing,
                final_amount=command.final_amount, payment_method=command.payment_method,
                parts=command.parts, company_share=command.company_share,
                technician_share=command.technician_share, idempotency_key=idempotency_key,
            )
        except InvalidJobActionError as exc:
            return ActionResult.failure("record_closing", job_id=command.job_id, message=str(exc))
        return ActionResult(
            success=True, action="record_closing", job_id=command.job_id, current_status=job["status"],
            idempotent_replay=was_replay, user_message="Closing recorded.",
        )

    def prepare_company_closing(self, job_id: int) -> ActionResult:
        """Computes the company-specific closing text via the existing
        closing.render_closing_from_persisted_fields() -- the one
        authority for this template, never a second one. Requires a
        structured amount + payment method already on record (never
        invents either); an unsupported company's closing template
        (e.g. Main Leads) surfaces as human_review_required, not a
        fabricated message."""
        job = storage.get_job_by_id(self.conn, job_id)
        if job is None:
            return ActionResult.failure("prepare_company_closing", job_id=job_id, message="No such job.")
        details = storage.get_latest_closing_details(self.conn, job_id)
        missing = []
        if not details or not details.get("amount"):
            missing.append("amount")
        if not details or not details.get("payment_method"):
            missing.append("payment method")
        if missing:
            return ActionResult(
                success=False, action="prepare_company_closing", job_id=job_id, human_review_required=True,
                human_review_reason=HumanReviewReason(code="missing_closing_fields", detail=f"Missing: {', '.join(missing)}"),
                user_message=f"HUMAN REVIEW NEEDED — missing: {', '.join(missing)}",
            )
        closing_input = ClosingInput(
            amount=details["amount"], payment_method=details["payment_method"], parts_amount=details.get("parts_amount"),
        )
        result = closing_module.render_closing_from_persisted_fields(
            job["source"], job["ticket_number"], job["fields"], closing_input,
        )
        if not result.supported:
            return ActionResult(
                success=False, action="prepare_company_closing", job_id=job_id, human_review_required=True,
                human_review_reason=HumanReviewReason(code="unsupported_closing", detail=result.reason),
                user_message=f"HUMAN REVIEW NEEDED — {result.reason}",
            )
        idempotency_key = compute_idempotency_key(job_id, "prepare_company_closing", result.text)
        was_replay = storage.get_event_by_idempotency_key(self.conn, idempotency_key) is not None
        try:
            updated = storage.mark_company_closing_prepared(self.conn, job_id, result.text, idempotency_key=idempotency_key)
        except InvalidJobActionError as exc:
            return ActionResult.failure("prepare_company_closing", job_id=job_id, message=str(exc))
        return ActionResult(
            success=True, action="prepare_company_closing", job_id=job_id, current_status=updated["status"],
            prepared_text=result.text, idempotent_replay=was_replay, user_message="Prepared.",
        )

    def confirm_company_closing_sent(self, job_id: int, prepared_text: str) -> ActionResult:
        idempotency_key = compute_idempotency_key(job_id, "confirm_company_closing_sent", prepared_text)
        was_replay = storage.get_event_by_idempotency_key(self.conn, idempotency_key) is not None
        try:
            job = storage.mark_company_closing_sent_manually(self.conn, job_id, prepared_text, idempotency_key=idempotency_key)
        except InvalidJobActionError as exc:
            return ActionResult.failure("confirm_company_closing_sent", job_id=job_id, message=str(exc))
        return ActionResult(
            success=True, action="confirm_company_closing_sent", job_id=job_id, current_status=job["status"],
            idempotent_replay=was_replay, user_message="Recorded as sent.",
        )

    def complete_job(self, job_id: int) -> ActionResult:
        idempotency_key = compute_idempotency_key(job_id, "complete_job")
        was_replay = storage.get_event_by_idempotency_key(self.conn, idempotency_key) is not None
        try:
            job = storage.complete_job(self.conn, job_id, idempotency_key=idempotency_key)
        except InvalidJobActionError as exc:
            return ActionResult.failure("complete_job", job_id=job_id, message=str(exc))
        return ActionResult(
            success=True, action="complete_job", job_id=job_id, current_status=job["status"],
            idempotent_replay=was_replay, user_message="Job completed.",
        )

    def update_technician_destinations(
        self, technician_id: int, dispatch_group: str, closing_group: str,
    ) -> ActionResult:
        """Explicit, transactional, audited change to one technician's
        WhatsApp dispatch/closing destination strings -- the only
        write-side technician-configuration command in this service.
        Not job-scoped (ActionResult.job_id is always None here).

        Validates the technician exists and both strings are non-blank.
        If the requested values already match what's persisted, this is
        a no-op (idempotent_replay=True, no new audit event) -- the same
        "no duplicate event for an unchanged fact" contract every other
        command in this file follows. To roll back, call this again with
        the previous values (returned by storage.update_technician_destinations());
        that is itself a new, equally audited transaction, never a
        destructive undo."""
        technician = storage.get_technician(self.conn, technician_id)
        if technician is None:
            return ActionResult.failure(
                "update_technician_destinations",
                message=f"No technician with id {technician_id}.",
            )

        issues = []
        if not (dispatch_group or "").strip():
            issues.append(ValidationIssue(field="dispatch_group", problem="Dispatch destination must not be blank."))
        if not (closing_group or "").strip():
            issues.append(ValidationIssue(field="closing_group", problem="Closing destination must not be blank."))
        if issues:
            return ActionResult.failure("update_technician_destinations", issues=issues)

        if technician["dispatch_group"] == dispatch_group and technician["closing_group"] == closing_group:
            return ActionResult(
                success=True, action="update_technician_destinations",
                idempotent_replay=True,
                user_message=f"{technician['name']}'s destinations already match -- no change made.",
            )

        result = storage.update_technician_destinations(self.conn, technician_id, dispatch_group, closing_group)
        return ActionResult(
            success=True, action="update_technician_destinations",
            audit_event_id=result["audit_event_id"],
            user_message=(
                f"Updated {result['name']}'s destinations. "
                f"dispatch_group: {result['previous_dispatch_group']!r} -> {result['dispatch_group']!r}; "
                f"closing_group: {result['previous_closing_group']!r} -> {result['closing_group']!r}."
            ),
        )

    # ------------------------------------------------------- Shadow Mode ---
    # Passive observation only -- see docs/SHADOW_MODE.md. None of these
    # commands ever change jobs.status/reconciliation_state, select a
    # technician, or send anything; every write here lands only in
    # shadow_episodes/shadow_events (see storage.py's own docstring for
    # that table pair). A ValidationIssue/failure here never blocks the
    # job's real operational commands above.

    def record_commercial_update(
        self, job_id: int, raw_technician_text: str,
        corrected_fields: Optional[dict] = None, dispatcher_actor: Optional[str] = None,
    ) -> ActionResult:
        """The core Shadow Mode observation point: the dispatcher pastes
        the technician's real WhatsApp text (step 1 of the Active Job
        commercial-update workflow). commercial.extract_commercial_update()
        computes a passive shadow suggestion; whatever the dispatcher
        actually confirms -- verbatim, or corrected via
        `corrected_fields` (any of quote_type/quote_amount/
        quote_range_low/quote_range_high/currency/customer_response) --
        becomes the verified commercial update the prepared company
        reply is built from. Never sends anything; never touches
        jobs.status."""
        if not raw_technician_text or not raw_technician_text.strip():
            return ActionResult.failure(
                "record_commercial_update", job_id=job_id,
                issues=[ValidationIssue(field="raw_technician_text", problem="the technician's actual message is required")],
            )
        job = storage.get_job_by_id(self.conn, job_id)
        if job is None:
            return ActionResult.failure("record_commercial_update", job_id=job_id, message=f"No job with id {job_id}.")

        suggestion = commercial.extract_commercial_update(raw_technician_text)
        corrected = bool(corrected_fields)
        if corrected:
            actual = dataclasses.replace(suggestion, **corrected_fields)
            neg_state, follow_up = commercial.derive_negotiation_state(actual.quote_type, actual.customer_response)
            actual = dataclasses.replace(
                actual, negotiation_state=neg_state, follow_up_required=follow_up,
                human_review_required=(actual.quote_type == commercial.QuoteType.UNKNOWN),
            )
        else:
            actual = suggestion

        base_key = compute_idempotency_key(
            job_id, "record_commercial_update", f"{raw_technician_text}|{sorted((corrected_fields or {}).items())}",
        )
        cu_key = f"{base_key}:CU"
        was_replay = storage.get_shadow_event_by_idempotency_key(self.conn, cu_key) is not None

        episode = storage.find_or_create_shadow_episode(
            self.conn, source=job["source"], channel=ShadowChannel.WHATSAPP_TECHNICIAN.value,
            ticket_or_pdl=job["ticket_number"], job_id=job_id, dispatcher_actor=dispatcher_actor,
            technician_id=job.get("assigned_technician_id"), previous_job_status=job["status"],
            incoming_message_type="COMMERCIAL_UPDATE",
        )
        text_fingerprint = _fingerprint(raw_technician_text)

        storage.record_shadow_event(
            self.conn, episode_id=episode["episode_id"], event_type=ShadowEventType.SHADOW_OBSERVATION.value,
            channel=ShadowChannel.WHATSAPP_TECHNICIAN.value, actor=dispatcher_actor,
            payload={"text_fingerprint": text_fingerprint, "job_id": job_id}, idempotency_key=f"{base_key}:OBS",
        )
        storage.record_shadow_event(
            self.conn, episode_id=episode["episode_id"], event_type=ShadowEventType.SHADOW_SUGGESTION.value,
            channel=ShadowChannel.SYSTEM_SHADOW_SUGGESTION.value,
            payload={**_commercial_update_to_dict(suggestion), "raw_technician_text": None, "text_fingerprint": text_fingerprint},
            idempotency_key=f"{base_key}:SUG",
        )
        storage.record_shadow_event(
            self.conn, episode_id=episode["episode_id"], event_type=ShadowEventType.COMMERCIAL_UPDATE.value,
            channel=ShadowChannel.WHATSAPP_TECHNICIAN.value, actor=dispatcher_actor,
            payload=_commercial_update_to_dict(actual), idempotency_key=cu_key,
        )
        comparison = compare_shadow_to_actual(
            shadow_suggested_action="record_commercial_update", dispatcher_actual_action="record_commercial_update",
            shadow_quote_type=suggestion.quote_type.value, dispatcher_quote_type=actual.quote_type.value,
            shadow_quote_amount=suggestion.quote_amount, dispatcher_quote_amount=actual.quote_amount,
            shadow_customer_response=suggestion.customer_response.value, dispatcher_customer_response=actual.customer_response.value,
            human_review_required=actual.human_review_required,
        )
        storage.record_shadow_event(
            self.conn, episode_id=episode["episode_id"], event_type=ShadowEventType.DISPATCHER_DECISION.value,
            channel=ShadowChannel.CRM_DISPATCHER_ACTION.value, actor=dispatcher_actor,
            payload={"comparison_label": comparison.value, "corrected": corrected}, idempotency_key=f"{base_key}:DEC",
        )

        suggested_reply = commercial.prepare_commercial_company_reply(job["source"], suggestion)
        actual_reply = commercial.prepare_commercial_company_reply(job["source"], actual)
        storage.update_shadow_episode(
            self.conn, episode["episode_id"],
            normalized_intent="COMMERCIAL_UPDATE",
            shadow_suggested_action="record_commercial_update",
            shadow_suggested_reply=suggested_reply.reply,
            shadow_confidence=suggestion.confidence,
            shadow_reasons=suggestion.validation_issues,
            validation_issues=actual.validation_issues,
            human_review_required=actual.human_review_required,
            dispatcher_actual_action="record_commercial_update",
            dispatcher_actual_reply=actual_reply.reply,
            commercial_update=_commercial_update_to_dict(actual),
            customer_response=actual.customer_response.value,
        )

        return ActionResult(
            success=True, action="record_commercial_update", job_id=job_id, idempotent_replay=was_replay,
            prepared_text=actual_reply.reply if actual_reply.confirmed else None,
            human_review_required=actual.human_review_required,
            human_review_reason=(
                HumanReviewReason(code="ambiguous_commercial_update", detail=actual_reply.reason)
                if not actual_reply.confirmed else None
            ),
            user_message=actual_reply.reply or actual_reply.reason,
        )

    def record_customer_response(
        self, job_id: int, customer_response: str, raw_text: Optional[str] = None, dispatcher_actor: Optional[str] = None,
    ) -> ActionResult:
        """A customer response fact arriving separately from a technician
        quote paste (e.g. a later note). Never implies cancellation on
        its own -- see commercial.derive_negotiation_state()."""
        valid = {s.value for s in commercial.CustomerResponseType}
        if customer_response not in valid:
            return ActionResult.failure(
                "record_customer_response", job_id=job_id,
                issues=[ValidationIssue(field="customer_response", problem=f"must be one of {sorted(valid)}")],
            )
        job = storage.get_job_by_id(self.conn, job_id)
        if job is None:
            return ActionResult.failure("record_customer_response", job_id=job_id, message=f"No job with id {job_id}.")

        episode = storage.find_or_create_shadow_episode(
            self.conn, source=job["source"], channel=ShadowChannel.CRM_DISPATCHER_ACTION.value,
            ticket_or_pdl=job["ticket_number"], job_id=job_id, dispatcher_actor=dispatcher_actor,
            previous_job_status=job["status"],
        )
        idempotency_key = compute_idempotency_key(job_id, "record_customer_response", f"{customer_response}|{raw_text or ''}")
        was_replay = storage.get_shadow_event_by_idempotency_key(self.conn, idempotency_key) is not None
        storage.record_shadow_event(
            self.conn, episode_id=episode["episode_id"], event_type=ShadowEventType.CUSTOMER_RESPONSE.value,
            channel=ShadowChannel.CRM_DISPATCHER_ACTION.value, actor=dispatcher_actor,
            payload={"customer_response": customer_response, "raw_text": raw_text}, idempotency_key=idempotency_key,
        )

        existing_cu = episode.get("commercial_update") or {}
        prior_quote_type = commercial.QuoteType(existing_cu.get("quote_type") or "UNKNOWN")
        response_enum = commercial.CustomerResponseType(customer_response)
        neg_state, follow_up = commercial.derive_negotiation_state(prior_quote_type, response_enum)
        merged_cu = dict(existing_cu)
        merged_cu["customer_response"] = customer_response
        merged_cu["negotiation_state"] = neg_state.value
        merged_cu["follow_up_required"] = follow_up
        storage.update_shadow_episode(
            self.conn, episode["episode_id"], customer_response=customer_response, commercial_update=merged_cu,
        )
        return ActionResult(
            success=True, action="record_customer_response", job_id=job_id, idempotent_replay=was_replay,
            user_message="Customer response recorded.",
        )

    def record_negotiation_outcome(
        self, job_id: int, negotiation_outcome: str, raw_text: Optional[str] = None, dispatcher_actor: Optional[str] = None,
    ) -> ActionResult:
        """The dispatcher-confirmed terminal negotiation fact (e.g.
        ACCEPTED, LOST_ON_PRICE) once it is actually known -- never
        inferred automatically from a single hesitant customer reply.
        `final_job_outcome` is populated from the job's own real status
        only when that status is already terminal (Closed/Cancelled);
        this never sets or changes jobs.status itself."""
        valid = {s.value for s in commercial.NegotiationState}
        if negotiation_outcome not in valid:
            return ActionResult.failure(
                "record_negotiation_outcome", job_id=job_id,
                issues=[ValidationIssue(field="negotiation_outcome", problem=f"must be one of {sorted(valid)}")],
            )
        job = storage.get_job_by_id(self.conn, job_id)
        if job is None:
            return ActionResult.failure("record_negotiation_outcome", job_id=job_id, message=f"No job with id {job_id}.")

        episode = storage.find_or_create_shadow_episode(
            self.conn, source=job["source"], channel=ShadowChannel.CRM_DISPATCHER_ACTION.value,
            ticket_or_pdl=job["ticket_number"], job_id=job_id, dispatcher_actor=dispatcher_actor,
            previous_job_status=job["status"],
        )
        idempotency_key = compute_idempotency_key(job_id, "record_negotiation_outcome", f"{negotiation_outcome}|{raw_text or ''}")
        was_replay = storage.get_shadow_event_by_idempotency_key(self.conn, idempotency_key) is not None
        final_outcome = job["status"] if job["status"] in (STATUS_CLOSED, STATUS_CANCELLED) else None
        storage.record_shadow_event(
            self.conn, episode_id=episode["episode_id"], event_type=ShadowEventType.NEGOTIATION_OUTCOME.value,
            channel=ShadowChannel.CRM_DISPATCHER_ACTION.value, actor=dispatcher_actor,
            payload={"negotiation_outcome": negotiation_outcome, "raw_text": raw_text}, idempotency_key=idempotency_key,
        )
        storage.update_shadow_episode(
            self.conn, episode["episode_id"], negotiation_outcome=negotiation_outcome, final_job_outcome=final_outcome,
        )
        return ActionResult(
            success=True, action="record_negotiation_outcome", job_id=job_id, idempotent_replay=was_replay,
            user_message="Negotiation outcome recorded.",
        )

    def answer_company_commercial_question(self, job_id: int, raw_question_text: str) -> ActionResult:
        """Correlates a source company's commercial question to this one
        job's latest verified quote/customer response only -- never
        another job, never a superseded quote. Returns
        human_review_required=True (no prepared_text) when no verified
        price is on record, rather than inventing one."""
        job = storage.get_job_by_id(self.conn, job_id)
        if job is None:
            return ActionResult.failure("answer_company_commercial_question", job_id=job_id, message=f"No job with id {job_id}.")

        question_type = commercial.detect_company_question(raw_question_text)
        latest = storage.get_latest_verified_commercial_update_for_job(self.conn, job_id)
        latest_cu = None
        if latest:
            latest_cu = commercial.CommercialUpdate(
                raw_technician_text=latest.get("raw_technician_text") or "",
                quote_type=commercial.QuoteType(latest.get("quote_type") or "UNKNOWN"),
                quote_amount=latest.get("quote_amount"),
                quote_range_low=latest.get("quote_range_low"),
                quote_range_high=latest.get("quote_range_high"),
                currency=latest.get("currency"),
                customer_response=commercial.CustomerResponseType(latest.get("customer_response") or "UNKNOWN"),
                human_review_required=bool(latest.get("human_review_required")),
            )
        answer = commercial.answer_company_commercial_question(question_type, latest_cu, source=job["source"])

        episode = storage.find_or_create_shadow_episode(
            self.conn, source=job["source"], channel=ShadowChannel.QUO_COMPANY.value,
            ticket_or_pdl=job["ticket_number"], job_id=job_id, previous_job_status=job["status"],
        )
        storage.record_shadow_event(
            self.conn, episode_id=episode["episode_id"], event_type=ShadowEventType.SHADOW_SUGGESTION.value,
            channel=ShadowChannel.SYSTEM_SHADOW_SUGGESTION.value,
            payload={
                "question_type": question_type.value, "question_fingerprint": _fingerprint(raw_question_text),
                "suggested_reply": answer.reply, "confirmed": answer.confirmed, "reason": answer.reason,
            },
        )

        human_review = not answer.confirmed
        return ActionResult(
            success=True, action="answer_company_commercial_question", job_id=job_id,
            prepared_text=answer.reply, human_review_required=human_review,
            human_review_reason=HumanReviewReason(code="commercial_question", detail=answer.reason) if human_review else None,
            user_message=answer.reply or answer.reason,
        )

    def confirm_commercial_reply_sent(
        self, job_id: int, sent_channel: str, dispatcher_actor: Optional[str] = None,
    ) -> ActionResult:
        """Records that the dispatcher actually sent the prepared
        commercial reply -- in the real Quo or company-WhatsApp thread,
        outside this application. This method cannot send anything
        itself; it only documents that a real send already happened.
        Distinct from `confirm_company_update_sent()` (the existing
        technician-status-update reply trail on the job's own `events`
        audit log) -- the commercial reply's own send-confirmation lives
        in the shadow episode, since a commercial update is not
        required to also be a recognized technician status type."""
        job = storage.get_job_by_id(self.conn, job_id)
        if job is None:
            return ActionResult.failure("confirm_commercial_reply_sent", job_id=job_id, message=f"No job with id {job_id}.")
        episode = storage.get_shadow_episode_for_job(self.conn, job_id)
        if episode is None:
            return ActionResult.failure(
                "confirm_commercial_reply_sent", job_id=job_id,
                message="No commercial update has been recorded for this job yet.",
            )
        storage.confirm_shadow_reply_sent(self.conn, episode["episode_id"], sent_channel, actor=dispatcher_actor)
        return ActionResult(
            success=True, action="confirm_commercial_reply_sent", job_id=job_id,
            user_message=f"Recorded as sent via {sent_channel}.",
        )

    def set_shadow_review(self, episode_id: str, review_status: str, reviewed_by: Optional[str] = None) -> ActionResult:
        """The only way an episode becomes export-eligible -- see
        shadow.EXPORTABLE_REVIEW_STATES / storage.set_shadow_review_status()."""
        try:
            storage.set_shadow_review_status(self.conn, episode_id, review_status, reviewed_by=reviewed_by)
        except ValueError as exc:
            return ActionResult.failure("set_shadow_review", message=str(exc))
        return ActionResult(success=True, action="set_shadow_review", user_message=f"Episode marked {review_status}.")

    # ------------------------------------------------------------ Queries -
    def get_job_workspace(self, job_id: int) -> Optional[dict]:
        """The current persisted state of one job -- a direct,
        undecorated pass-through to storage.get_job_by_id(). No
        Streamlit dependency."""
        return storage.get_job_by_id(self.conn, job_id)

    def get_job_history(self, job_id: int) -> List[dict]:
        """The full append-only chronological event history for one job
        -- a direct pass-through to storage.get_audit_timeline_by_job_id().
        No Streamlit dependency."""
        return storage.get_audit_timeline_by_job_id(self.conn, job_id)

    def get_queue(self, kind: str) -> list:
        """Convenience read for the four operational queues. This
        module never imports the repo-root read model itself (see
        QueueReader's own docstring for why) -- a caller must inject a
        QueueReader implementation via the constructor. Raises
        RuntimeError, not an import error, when none was provided, so
        the failure is obviously about missing wiring, not a broken
        import path."""
        if self.queue_reader is None:
            raise RuntimeError(
                "DispatcherService.get_queue() requires a QueueReader to be injected "
                "via the constructor -- none was provided. See docs/ARCHITECTURE_FOUNDATION.md."
            )
        return self.queue_reader.read_queue(self.conn, kind)
