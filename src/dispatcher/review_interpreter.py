"""Future AI-review seam -- disabled by default, no LLM dependency.

Defines the interface a future review interpreter (deterministic-rules-
based, or eventually LLM-backed) would implement, plus the only
implementation that exists in this build: DisabledReviewInterpreter,
which always declines and never touches anything. Python application
logic (application.py's DispatcherService, storage.derive_job_status())
remains final authority regardless of what any interpreter suggests --
nothing in this codebase currently reads a ReviewSuggestion and acts on
it automatically.

A ReviewInterpreter implementation must never:
  - write to SQLite;
  - change job status;
  - select a technician;
  - invent availability, prices, or completion;
  - send a message.

No external LLM provider is required for the test suite or the
operational MVP -- DisabledReviewInterpreter is the default and the
only one wired into application.py in this build.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol


@dataclass(frozen=True)
class ReviewSuggestion:
    """Advisory only. Nothing in this codebase currently consumes this
    to change stored state -- a suggestion is a value a caller may
    choose to *display*, never a value that reaches storage.py on its
    own."""

    suggested_message_type: Optional[str] = None
    suggested_extracted_fields: dict = field(default_factory=dict)
    confidence: int = 0
    reasons: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    recommended_final_action: Optional[str] = None
    human_review_required: bool = True


class ReviewInterpreter(Protocol):
    def review(self, message: str, current_job_state: Optional[dict]) -> ReviewSuggestion: ...

    def is_enabled(self) -> bool: ...


class DisabledReviewInterpreter:
    """The only ReviewInterpreter implementation wired into
    DispatcherService in this build. Always returns a suggestion with
    human_review_required=True and zero confidence -- structurally
    incapable of driving an automatic decision, not merely configured
    not to."""

    name = "DisabledReviewInterpreter"

    def is_enabled(self) -> bool:
        return False

    def review(self, message: str, current_job_state: Optional[dict]) -> ReviewSuggestion:
        return ReviewSuggestion(
            reasons=["AI review is disabled in this build -- no interpreter is configured."],
            human_review_required=True,
        )


class DeterministicRulesReviewInterpreter:
    """Optional, deterministic (never "AI") implementation: reuses the
    existing parser/classifier outputs verbatim rather than
    re-interpreting anything. Not wired into DispatcherService by
    default -- exists only to show what a non-LLM ReviewInterpreter
    looks like, using facts the codebase already computes. Still never
    writes to storage, selects a technician, or invents data; it only
    repackages dispatcher.parser.extract_job() /
    dispatcher.storage.classify_incoming()'s own results as a
    ReviewSuggestion."""

    name = "DeterministicRulesReviewInterpreter"

    def is_enabled(self) -> bool:
        return True

    def review(self, message: str, current_job_state: Optional[dict]) -> ReviewSuggestion:
        from .parser import extract_job, load_vehicle_rules

        job = extract_job(message, load_vehicle_rules())
        missing = [name for name, fv in job.all_fields().items() if fv.is_missing]
        conflicting = [name for name, fv in job.all_fields().items() if fv.is_conflicting]
        return ReviewSuggestion(
            suggested_message_type=None,  # classification needs a live DB connection; left to the caller
            suggested_extracted_fields={name: fv.value for name, fv in job.all_fields().items()},
            confidence=job.overall_confidence,
            reasons=list(job.status_reasons),
            conflicts=conflicting,
            recommended_final_action=None,
            human_review_required=bool(missing or conflicting),
        )
