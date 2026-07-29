"""Small typed models for the application service layer (application.py).

Deliberately lean -- no validation framework, no ORM-style model base
class. Every field here is either read directly by a caller (Streamlit
page, test, future CLI/API adapter) or passed straight through to an
existing deterministic authority (storage.py, policy.py, closing.py,
updates.py). These types describe shapes that already flow through the
codebase today; they do not introduce new business rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class ValidationIssue:
    """One reason a command's input was rejected before anything was
    persisted -- e.g. a missing required field, an unrecognized
    technician-update type. Distinct from HumanReviewReason, which
    describes why the *system* cannot safely decide something on its
    own even though the input itself was well-formed."""

    field: str
    problem: str


@dataclass(frozen=True)
class HumanReviewReason:
    """Why a command could not proceed deterministically and instead
    requires a human decision -- e.g. a Custom technician update with no
    deterministic company reply, a closing missing amount/payment
    method, an unsupported company closing template."""

    code: str
    detail: str


@dataclass(frozen=True)
class PreparedMessage:
    """A deterministically-formatted, not-yet-sent message plus its
    intended destination -- the shape every "prepare X" command returns.
    `supported=False` means the formatter itself declined (e.g. Main
    Leads has no closing template) -- `reason` explains why."""

    text: Optional[str]
    destination: Optional[str] = None
    supported: bool = True
    reason: str = ""


@dataclass(frozen=True)
class EligibleTechnicianResult:
    """Routing output for one job -- every technician with zero
    eligibility problems against the job's resolved area/service/
    vehicle, plus the resolution itself. Never reduces multiple eligible
    technicians to one; presenting the full list is the caller's job."""

    technicians: List[dict] = field(default_factory=list)
    canonical_area: Optional[str] = None
    market_label: Optional[str] = None
    routing_reason: str = ""


@dataclass(frozen=True)
class DispatchPreparation:
    """Everything the New Jobs workspace needs to show before a
    dispatcher commits to WhatsApp dispatch: the formatted Luxor
    technician message, routing/eligibility, and whether an
    acknowledgment is required first."""

    prepared_text: str
    eligible: EligibleTechnicianResult
    destination: Optional[str]
    acknowledgment_required: bool
    acknowledgment_text: Optional[str]


@dataclass(frozen=True)
class TechnicianUpdateCommand:
    job_id: int
    update_type: str
    raw_update: str
    eta_minutes: Optional[int] = None


@dataclass(frozen=True)
class ClosingCommand:
    job_id: int
    raw_closing: str
    final_amount: Optional[str] = None
    payment_method: Optional[str] = None
    parts: Optional[str] = None
    company_share: Optional[str] = None
    technician_share: Optional[str] = None


@dataclass(frozen=True)
class JobCommandContext:
    """Minimal identity/actor context threaded through a command --
    `actor` is always "dispatcher" today (every write in this system is
    a human-confirmed action); kept as a field rather than hardcoded so
    a future authenticated-multi-user build doesn't need a signature
    change here."""

    job_id: int
    actor: str = "dispatcher"


@dataclass
class ActionResult:
    """The one structured return shape every state-changing
    DispatcherService command produces -- callers (Streamlit pages,
    tests, a future CLI/API adapter) branch on these fields instead of
    inspecting an arbitrary dict."""

    success: bool
    action: str
    job_id: Optional[int] = None
    previous_status: Optional[str] = None
    current_status: Optional[str] = None
    prepared_text: Optional[str] = None
    validation_issues: List[ValidationIssue] = field(default_factory=list)
    human_review_required: bool = False
    human_review_reason: Optional[HumanReviewReason] = None
    audit_event_id: Optional[int] = None
    idempotent_replay: bool = False
    user_message: str = ""

    @classmethod
    def failure(cls, action: str, *, job_id: Optional[int] = None, issues: Optional[List[ValidationIssue]] = None, message: str = "") -> "ActionResult":
        return cls(
            success=False, action=action, job_id=job_id,
            validation_issues=issues or [], user_message=message,
        )
