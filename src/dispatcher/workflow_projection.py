"""Single source of truth for "what does the operator need to do about
this [message|job] right now" -- two pure functions covering the whole
real lifecycle (pre-commit and post-commit), sharing one fixed
next_action vocabulary. Every CRM screen that shows a next-action label
(Active, Closings, New Jobs, the Action Center) must call one of these
instead of computing its own signal, so there is exactly one place this
logic lives, for either half of the lifecycle.

Both functions are pure: each takes a plain dict of already-gathered
facts and returns a WorkflowProjection -- neither touches a database.

Why two functions instead of one: a raw incoming message and a
committed job are genuinely different entities with different
persisted state (quo_pending_messages + dispatch_tracking vs. jobs +
events), and forcing pre-commit facts into jobs.status was explicitly
out of scope. What they share is the *vocabulary* (NEXT_ACTIONS below)
and the *contract* (WorkflowProjection) -- Action Center normalizes
both into one item schema (see operator_api.py's
_normalize_action_center_item()) without either function needing to
know about the other.

  derive_pending_message_next_action(ctx) -- covers REVIEW_NEW_JOB,
    ACK_COMPANY_K, SEND_JOB_TO_TECHNICIAN: everything from "message
    arrived" through "dispatched to technician" (the action that
    creates the job row -- see below).

  derive_next_action(ctx) -- covers ASSIGN_TECHNICIAN through COMPLETE:
    everything from "job exists" onward. ASSIGN_TECHNICIAN stays here
    (not pre-commit) because re-assigning a technician on an existing
    job is storage.assign_technician(), a distinct, later, committed-job
    action, unrelated to the technician auto-suggested at dispatch time.

Earlier revisions of this module claimed ACK_COMPANY_K and
SEND_JOB_TO_TECHNICIAN were structurally unreachable. That was wrong,
not architectural: both are real, reachable pre-commit steps
(acknowledge_job_manually() / prepare_technician_dispatch() +
confirm_whatsapp_dispatch(), both keyed by quo_pending_messages'
message_id) -- they just weren't covered by derive_next_action()'s
committed-job-only contract. derive_pending_message_next_action() below
closes that gap. See tests/test_workflow_projection.py for the
reachability proof.

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


def derive_pending_message_next_action(ctx: dict) -> WorkflowProjection:
    """`ctx` keys (from operator_api._new_job_dto's shape -- see that
    function for exactly how each is computed):

      needs_review    -- bool, missing/conflicting required fields
      k_required      -- bool, this source (AGN/Lucky) requires a "K"
                          acknowledgment before dispatch (policy.py)
      k_sent          -- bool, dispatch_tracking.acknowledged_at is set
      eligible_count   -- int, how many technicians are eligible for
                          this job's area/service (routing.py) -- 0
                          means the system cannot safely suggest one
      dispatched      -- bool, dispatch_tracking.dispatched_manually_at
                          is set (in practice always False for anything
                          this function is called on: a dispatched
                          message is marked "actioned" and no longer
                          appears in the live queue this data comes
                          from -- kept as an explicit check for honesty,
                          not because it's expected to ever be True here)

    Precedence:
      1. REVIEW_NEW_JOB -- needs_review: missing/conflicting fields, a
         human must resolve before anything else is safe to decide.
      2. ACK_COMPANY_K -- k_required and not k_sent.
      3. HUMAN_REVIEW_NEEDED -- eligible_count == 0: no technician can
         be safely suggested; never guessed.
      4. SEND_JOB_TO_TECHNICIAN -- reviewed, K resolved (or not
         required), a technician is eligible, not yet dispatched: ready
         to prepare and confirm the WhatsApp handoff.
      5. Fallback (dispatched already -- see the `dispatched` note
         above): COMPLETE/terminal for *this* projection, meaning this
         message has graduated into a committed job; the caller should
         be using derive_next_action() for it instead by this point.
    """
    if ctx.get("needs_review"):
        return WorkflowProjection("REVIEW_NEW_JOB", "Needs dispatcher review.", False, False)
    if ctx.get("k_required") and not ctx.get("k_sent"):
        return WorkflowProjection("ACK_COMPANY_K", "Company acknowledgment (K) required before dispatch.", False, False)
    if not ctx.get("eligible_count"):
        return WorkflowProjection("HUMAN_REVIEW_NEEDED", "No eligible technician found for this job's area/service.", True, False)
    if not ctx.get("dispatched"):
        return WorkflowProjection("SEND_JOB_TO_TECHNICIAN", "Ready to dispatch to technician.", False, False)
    return WorkflowProjection("COMPLETE", "Already dispatched -- now tracked as a committed job.", False, True)


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
      3. (ACK_COMPANY_K -- pre-commit only; see derive_pending_message_next_action().)
      4. ASSIGN_TECHNICIAN -- Approved, no technician assigned yet.
      5. (SEND_JOB_TO_TECHNICIAN -- pre-commit only; see derive_pending_message_next_action().)
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


# ============================================================== LIFECYCLE
LIFECYCLE_STEPS = (
    "Received", "Reviewed", "Company acknowledged", "Technician assigned",
    "Sent to technician", "Technician accepted", "Work/update", "Company updated",
    "Closing received", "Closing sent", "Complete",
)

STEP_COMPLETED = "completed"
STEP_CURRENT = "current"
STEP_BLOCKED = "blocked"
STEP_PENDING = "pending"


def derive_lifecycle_steps(ctx: dict) -> list:
    """Renders the same 11-step lifecycle for either an incoming message
    or a committed job, from facts already gathered elsewhere -- never a
    second, independent calculation (see this module's own precedence
    functions, which supply most of the same `ctx`). Frontends must call
    this (via operator_api's normalized item) rather than infer step
    completion in JavaScript.

    `ctx` keys (all optional -- whichever half doesn't apply for a given
    entity_type is simply absent):

      entity_type          -- "INCOMING_MESSAGE" or "JOB" (default "JOB")
      needs_review, k_required, k_sent, eligible_count, dispatched
                            -- pending-message facts (see derive_pending_message_next_action)
      technician_name, technician_k, latest_technician_update,
      company_update_owed, closing, status, reconciliation_state
                            -- committed-job facts (see derive_next_action)
      human_review_needed  -- the already-computed next_action's flag,
                              so this function never re-derives precedence

    Returns exactly 11 {"step": 1-11, "label": ..., "status": one of
    completed/current/blocked/pending} entries. At most one step is ever
    "current" or "blocked" -- the first not-yet-completed step in order;
    everything before it is "completed", everything after is "pending".
    A dead-terminal job (Cancelled/Rejected) never gets a "current" step
    -- nothing is actionable anymore, so every remaining incomplete step
    is "pending", not "current".
    """
    entity_type = ctx.get("entity_type", "JOB")
    status = ctx.get("status")
    technician_k = ctx.get("technician_k")
    tk_status = technician_k.get("status") if technician_k else None
    latest_update = ctx.get("latest_technician_update")
    closing = ctx.get("closing")

    completed = [False] * 11
    completed[0] = True  # Received -- an item exists.

    if entity_type == "INCOMING_MESSAGE":
        completed[1] = not ctx.get("needs_review", False)
        completed[2] = (not ctx.get("k_required")) or bool(ctx.get("k_sent"))
        completed[3] = bool(ctx.get("eligible_count"))
        completed[4] = bool(ctx.get("dispatched"))
    else:
        completed[1] = status not in (None, "New", "Needs Review")
        completed[2] = True  # already true by construction post-commit -- see module docstring
        completed[3] = bool(ctx.get("technician_name"))
        completed[4] = True  # job exists -> confirm_whatsapp_dispatch already ran

    completed[5] = tk_status == "accepted"
    completed[6] = bool(latest_update)
    completed[7] = bool(latest_update) and not ctx.get("company_update_owed")
    completed[8] = status == "Closed"
    completed[9] = bool(closing and closing.get("sent_confirmed"))
    completed[10] = status in ("Cancelled", "Rejected") or (status == "Closed" and ctx.get("reconciliation_state") == "Settled")

    dead_terminal = status in ("Cancelled", "Rejected")
    human_review_needed = bool(ctx.get("human_review_needed"))
    tk_declined = tk_status == "declined"

    steps = []
    current_assigned = False
    for i, label in enumerate(LIFECYCLE_STEPS):
        if completed[i]:
            step_status = STEP_COMPLETED
        elif dead_terminal:
            step_status = STEP_PENDING
        elif not current_assigned:
            if i == 5 and tk_declined:
                step_status = STEP_BLOCKED
            elif human_review_needed:
                step_status = STEP_BLOCKED
            else:
                step_status = STEP_CURRENT
            current_assigned = True
        else:
            step_status = STEP_PENDING
        steps.append({"step": i + 1, "label": label, "status": step_status})
    return steps
