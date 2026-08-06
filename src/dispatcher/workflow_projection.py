"""Single source of truth for "what does the operator need to do about
this job right now" -- one pure function, explicit precedence, the
fixed next_action vocabulary the product spec requires. Every CRM
screen that shows a next-action label (Active, Closings, the Action
Center) must call derive_next_action() instead of computing its own
signal, so there is exactly one place this logic lives.

This function is pure: it takes a plain dict of already-gathered facts
about one job (never a live DB connection) and returns a
WorkflowProjection. Gathering that input dict is operator_api.py's
_build_workflow_context(), which reuses the exact same per-job DTOs
every existing screen already computes (_technician_k_dto,
_company_update_owed_dto, _closing_dto) -- this module adds no new
database queries of its own.

Two architectural notes on why 2 of the 11 allowed next_action values
can never actually be *returned* by this function for an
already-committed job (both stay in NEXT_ACTIONS because the vocabulary
is shared with New Jobs' pending-message review, a different,
message-keyed code path this function's "committed jobs only" contract
does not cover):

- ACK_COMPANY_K: acknowledge_job_manually() runs against a *pending
  message*, strictly before confirm_whatsapp_dispatch() commits the
  job. By the time a jobs row exists, the acknowledgment question (for
  sources that require one) has already been resolved one way or the
  other -- there is no "K still owed" state left to detect on a
  committed job in this schema.
- SEND_JOB_TO_TECHNICIAN: confirm_whatsapp_dispatch() -- the action
  that creates the job row in the first place -- IS the "sent to
  technician" milestone. What can still be open post-commit is *which*
  technician (ASSIGN_TECHNICIAN), never whether dispatch happened.

See docs/END_TO_END_WORKFLOW_AUDIT.md for the fuller architecture
writeup this derives from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

NEXT_ACTIONS = (
    "REVIEW_NEW_JOB",
    "ACK_COMPANY_K",
    "ASSIGN_TECHNICIAN",
    "SEND_JOB_TO_TECHNICIAN",
    "WAIT_TECHNICIAN_K",
    "REVIEW_TECHNICIAN_UPDATE",
    "UPDATE_COMPANY",
    "REVIEW_CLOSING",
    "SEND_CLOSING_TO_COMPANY",
    "COMPLETE",
    "HUMAN_REVIEW_NEEDED",
)


@dataclass(frozen=True)
class WorkflowProjection:
    next_action: str
    blocking_reason: str
    human_review_needed: bool
    terminal: bool  # True for Closed+Settled, Cancelled, Rejected -- nothing further is ever owed


def _technician_k_all_ambiguous(technician_k: dict) -> bool:
    candidates = technician_k.get("candidates") or []
    return bool(candidates) and all(c.get("category") == "AMBIGUOUS" for c in candidates)


def derive_next_action(ctx: dict) -> WorkflowProjection:
    """`ctx` keys (all optional except `status`):

      status                  -- jobs.status (required)
      reconciliation_state    -- jobs.reconciliation_state
      technician_name         -- assigned technician's name, or None
      technician_k            -- the technician_k DTO shape (see
                                  operator_api._technician_k_dto), or None
      latest_technician_update -- {"update_type": ..., ...} or None
      company_update_owed     -- the company_update_owed DTO, or None
      closing                 -- {"missing": [...], "supported": bool|None,
                                  "sent_confirmed": bool, "unsupported_reason": str|None},
                                  populated only when status == "Closed"
                                  (see operator_api._closing_dto -- it
                                  only computes real values for that
                                  status anyway), else None.

    Precedence (checked in this exact order -- the first matching rung
    wins, every branch returns immediately, none overlap):

      0. Terminal states (Cancelled/Rejected, or Closed+Settled) --
         checked first because nothing else is ever owed regardless of
         what else might look "wrong" about the job's other fields.
      1. Human review blockers -- Handle Manually status, a Custom
         technician update (no deterministic company reply exists), a
         technician decline (needs reassignment), an ambiguous-only K
         match (system can't disambiguate on its own), or an
         unsupported company closing template. Checked before every
         later rung because none of them are safe to act on until a
         person resolves these.
      2. REVIEW_NEW_JOB -- status == "Needs Review".
      3. (ACK_COMPANY_K -- see module docstring: unreachable here.)
      4. ASSIGN_TECHNICIAN -- Approved, no technician assigned yet.
      5. (SEND_JOB_TO_TECHNICIAN -- see module docstring: unreachable here.)
      6. WAIT_TECHNICIAN_K -- Approved, technician assigned, no
         technician status update recorded yet (covers both "still
         waiting on the K reply" and, once K is confirmed accepted,
         "waiting on the technician's first real status update" -- the
         fixed vocabulary has no separate label for the latter; the
         blocking_reason text still distinguishes them).
      7. REVIEW_TECHNICIAN_UPDATE -- technician reported "Completed"
         but the job isn't Closed yet (closing hasn't been recorded).
      8. UPDATE_COMPANY -- a technician update exists that the company
         hasn't been told about yet.
      9./10. REVIEW_CLOSING / SEND_CLOSING_TO_COMPANY -- Closed with
         missing closing fields, or complete-but-not-yet-sent.
      11. COMPLETE -- closing sent, ready for the explicit "mark
          complete" action (or already terminal, per rung 0).

    Fallback (no rung above matched -- e.g. In Progress, a real
    non-Custom/non-Completed update already answered to the company,
    closing not applicable yet): WAIT_TECHNICIAN_K, reason "waiting for
    the next technician update" -- the same reasoning as rung 6's
    "no label for mid-job waiting" limitation, documented once here
    rather than repeated at each call site.
    """
    status = ctx.get("status")

    if status in ("Cancelled", "Rejected"):
        return WorkflowProjection("COMPLETE", "Closed out -- no further action.", False, True)
    if status == "Closed" and ctx.get("reconciliation_state") == "Settled":
        return WorkflowProjection("COMPLETE", "Job complete.", False, True)

    latest_update = ctx.get("latest_technician_update")
    technician_k = ctx.get("technician_k")
    closing = ctx.get("closing")

    if status == "Handle Manually":
        return WorkflowProjection("HUMAN_REVIEW_NEEDED", "Marked Handle Manually.", True, False)
    if latest_update and latest_update.get("update_type") == "Custom":
        return WorkflowProjection(
            "HUMAN_REVIEW_NEEDED", "Technician sent a Custom update -- no deterministic company reply.", True, False,
        )
    if technician_k and technician_k.get("status") == "declined":
        return WorkflowProjection("HUMAN_REVIEW_NEEDED", "Technician declined -- needs reassignment.", True, False)
    if technician_k and technician_k.get("status") == "waiting" and _technician_k_all_ambiguous(technician_k):
        return WorkflowProjection(
            "HUMAN_REVIEW_NEEDED", "Technician K reply is ambiguous between multiple waiting jobs.", True, False,
        )
    if closing and closing.get("supported") is False:
        return WorkflowProjection(
            "HUMAN_REVIEW_NEEDED", closing.get("unsupported_reason") or "Closing format not supported for this company.", True, False,
        )

    if status == "Needs Review":
        return WorkflowProjection("REVIEW_NEW_JOB", "Needs dispatcher review.", False, False)

    if status == "Approved" and not ctx.get("technician_name"):
        return WorkflowProjection("ASSIGN_TECHNICIAN", "No technician assigned.", False, False)

    if status == "Approved" and not latest_update:
        if technician_k and technician_k.get("status") == "accepted":
            reason = "Technician accepted -- waiting for the first status update."
        else:
            reason = "Waiting on technician K (acceptance)."
        return WorkflowProjection("WAIT_TECHNICIAN_K", reason, False, False)

    if latest_update and latest_update.get("update_type") == "Completed" and status != "Closed":
        return WorkflowProjection("REVIEW_TECHNICIAN_UPDATE", "Technician reported job completed -- record the closing.", False, False)

    if ctx.get("company_update_owed"):
        return WorkflowProjection("UPDATE_COMPANY", "A company status update is owed.", False, False)

    if closing:
        if closing.get("missing"):
            return WorkflowProjection("REVIEW_CLOSING", f"Missing: {', '.join(closing['missing'])}.", False, False)
        if not closing.get("sent_confirmed"):
            return WorkflowProjection("SEND_CLOSING_TO_COMPANY", "Closing ready -- not yet sent.", False, False)
        return WorkflowProjection("COMPLETE", "Closing sent -- ready to mark complete.", False, False)

    return WorkflowProjection("WAIT_TECHNICIAN_K", "Waiting for the next technician update.", False, False)
