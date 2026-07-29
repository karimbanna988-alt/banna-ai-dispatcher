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

import hashlib
import sqlite3
from typing import List, Optional

from . import closing as closing_module
from . import storage, updates
from .action_names import ActionName
from .adapters import DisabledQuoOutboundAdapter, ManualWhatsAppHandoffAdapter, QuoOutboundAdapter, WhatsAppHandoffAdapter
from .app_models import (
    ActionResult,
    ClosingCommand,
    HumanReviewReason,
    TechnicianUpdateCommand,
    ValidationIssue,
)
from .closing import ClosingInput
from .storage import InvalidJobActionError


def _fingerprint(text: str) -> str:
    """Same normalization luxor_desk_read_model._content_fingerprint()
    already uses -- whitespace/case-insensitive, so two submissions that
    differ only in incidental formatting still collapse to the same
    idempotency key, matching the same tolerance the New Jobs dedup
    logic already applies to inbound messages."""
    normalized = " ".join((text or "").split()).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def compute_idempotency_key(identity, action: str, fingerprint_text: str = "") -> str:
    """Deterministic: the same (identity, action, fingerprint_text)
    always produces the same key. `identity` is a job_id (int) for
    already-committed jobs, or a quo_pending_messages.message_id (str)
    for pending-message-level actions (acknowledge, confirm dispatch) --
    either way, always something that already uniquely identifies the
    thing being acted on, never invented."""
    basis = f"{identity}|{action}|{_fingerprint(fingerprint_text)}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


class DispatcherService:
    """One instance per request/page-render, wrapping one
    sqlite3.Connection -- mirrors how every luxor_desk_pages/*.py file
    already does `conn = storage.connect()` once per render."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        whatsapp_adapter: Optional[WhatsAppHandoffAdapter] = None,
        quo_adapter: Optional[QuoOutboundAdapter] = None,
    ) -> None:
        self.conn = conn
        self.whatsapp_adapter = whatsapp_adapter or ManualWhatsAppHandoffAdapter()
        self.quo_adapter = quo_adapter or DisabledQuoOutboundAdapter()

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
        """Convenience read for the four operational queues. Unlike
        every command above, this method lazily imports the existing
        repo-root read model (luxor_desk_read_model.py /
        luxor_ops_shared.py) rather than reimplementing its display-
        formatting/dedup logic a second time -- "do not duplicate
        storage logic" applies here too, and that logic already exists,
        tested, in one place. This is the one method on this class with
        a soft dependency on the repo root being importable (true
        whenever a luxor_desk_pages/*.py caller reaches this point,
        since it already added the repo root to sys.path to import this
        module in the first place)."""
        import luxor_desk_read_model as rm  # local import: lives at repo root, not under src/

        if kind == "new_jobs":
            live, _backlog, _other = rm.get_live_dispatch_queue(self.conn)
            return live
        if kind == "active":
            return rm.get_active_jobs_detailed(self.conn)
        if kind == "closings":
            return rm.get_closing_candidates(self.conn)
        if kind == "completed":
            return rm.get_completed_jobs(self.conn)
        raise ValueError(f"Unknown queue kind: {kind!r} -- must be one of new_jobs/active/closings/completed")
