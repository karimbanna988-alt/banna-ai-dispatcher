"""The accept/pass/needs-review/handle-manually decision engine.

Combines: the parser's own hard HANDLE_MANUALLY override, the permanent
service-area mapping, today's daily rules, and vehicle/service restrictions.
Never guesses -- every branch that can't confidently produce ACCEPT or PASS
falls back to HANDLE_MANUALLY. See docs/architecture.md section 3 for the
data contracts this was designed against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional

from ..companies.base import CompanyProfile
from ..models import ExtractedJob, ExtractionStatus
from ..parser import REQUIRED_FIELDS
from .daily import DailyRules
from .service_areas import ServiceAreaRecord, find_service_area

# Below this confidence, a would-be ACCEPT/PASS is downgraded to NEEDS_REVIEW
# so the dispatcher double-checks it before approving, without treating it
# as a full HANDLE_MANUALLY (the job data itself was extractable and clean).
NEEDS_REVIEW_CONFIDENCE_THRESHOLD = 80


class Verdict(str, Enum):
    ACCEPT = "ACCEPT"
    PASS = "PASS"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    HANDLE_MANUALLY = "HANDLE_MANUALLY"


@dataclass
class Decision:
    verdict: Verdict
    confidence: int
    reasons: List[str] = field(default_factory=list)
    missing_information: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    restrictions_triggered: List[str] = field(default_factory=list)
    dispatch_group: Optional[str] = None
    quo_reply_suggestion: Optional[str] = None


def force_handle_manually(decision: Decision, reason: str) -> Decision:
    """Escalate an already-computed Decision to HANDLE_MANUALLY, prepending
    `reason`. For cross-cutting overrides decided outside decide()'s own
    area/vehicle/daily-rule checks -- e.g. the orchestration layer forces
    this when storage.classify_incoming() reports a closing arriving with
    no prior dispatch for its ticket (an anomaly that must never be treated
    as a normal accept/pass job)."""
    decision.verdict = Verdict.HANDLE_MANUALLY
    decision.reasons = [reason] + decision.reasons
    return decision


def _missing_and_conflicting(job: ExtractedJob):
    fields = job.all_fields()
    missing = [name for name in REQUIRED_FIELDS if fields[name].is_missing]
    conflicts = [f"{name}: {fields[name].candidates}" for name, fv in fields.items() if fv.is_conflicting]
    return missing, conflicts


def decide(
    job: ExtractedJob,
    profile: CompanyProfile,
    service_areas: List[ServiceAreaRecord],
    daily_rules: Optional[DailyRules] = None,
    now: Optional[datetime] = None,
) -> Decision:
    missing, conflicts = _missing_and_conflicting(job)
    restrictions_triggered = (
        [job.vehicle_restriction_reason] if job.vehicle_restriction_status in ("restricted", "unclear") else []
    )

    # Hard override: the parser already determined this can't be trusted.
    if job.status == ExtractionStatus.HANDLE_MANUALLY:
        return Decision(
            verdict=Verdict.HANDLE_MANUALLY,
            confidence=job.overall_confidence,
            reasons=list(job.status_reasons),
            missing_information=missing,
            conflicts=conflicts,
            restrictions_triggered=restrictions_triggered,
        )

    area = find_service_area(job.city.value, job.state.value, service_areas)

    if area.status == "not_found":
        return Decision(
            verdict=Verdict.HANDLE_MANUALLY, confidence=job.overall_confidence,
            reasons=[area.reason], missing_information=missing, conflicts=conflicts,
            restrictions_triggered=restrictions_triggered,
        )

    if area.status == "unconfirmed":
        return Decision(
            verdict=Verdict.HANDLE_MANUALLY, confidence=job.overall_confidence,
            reasons=[area.reason], missing_information=missing, conflicts=conflicts,
            restrictions_triggered=restrictions_triggered,
        )

    if area.status == "inactive":
        return Decision(
            verdict=Verdict.PASS, confidence=job.overall_confidence,
            reasons=[area.reason], missing_information=missing, conflicts=conflicts,
            restrictions_triggered=restrictions_triggered,
        )

    # area.status == "confirmed" from here on.
    dispatch_group = area.dispatch_group

    if daily_rules is None:
        return Decision(
            verdict=Verdict.HANDLE_MANUALLY, confidence=job.overall_confidence,
            reasons=["no daily rules provided for today -- cannot confirm current area/technician coverage"],
            missing_information=missing, conflicts=conflicts, restrictions_triggered=restrictions_triggered,
            dispatch_group=dispatch_group,
        )

    if daily_rules.is_expired(now):
        return Decision(
            verdict=Verdict.HANDLE_MANUALLY, confidence=job.overall_confidence,
            reasons=["today's temporary rules have expired -- confirm current coverage before deciding"],
            missing_information=missing, conflicts=conflicts, restrictions_triggered=restrictions_triggered,
            dispatch_group=dispatch_group,
        )

    area_labels = {v for v in (job.city.value, job.state.value, area.record.city, area.record.state) if v}
    area_labels_lower = {a.lower() for a in area_labels}

    if any(closed.lower() in area_labels_lower for closed in daily_rules.closed_areas):
        return Decision(
            verdict=Verdict.PASS, confidence=job.overall_confidence,
            reasons=["area is marked closed in today's rules"], missing_information=missing,
            conflicts=conflicts, restrictions_triggered=restrictions_triggered, dispatch_group=dispatch_group,
        )

    tech_status = None
    for area_name, status in daily_rules.technician_status.items():
        if area_name.lower() in area_labels_lower or (dispatch_group and area_name.lower() == dispatch_group.lower()):
            tech_status = status
            break

    if tech_status in ("off", "busy"):
        return Decision(
            verdict=Verdict.PASS, confidence=job.overall_confidence,
            reasons=[f"technician for this area is {tech_status} today"], missing_information=missing,
            conflicts=conflicts, restrictions_triggered=restrictions_triggered, dispatch_group=dispatch_group,
        )

    if job.vehicle_restriction_status == "restricted":
        return Decision(
            verdict=Verdict.PASS, confidence=job.overall_confidence,
            reasons=[job.vehicle_restriction_reason], missing_information=missing,
            conflicts=conflicts, restrictions_triggered=restrictions_triggered, dispatch_group=dispatch_group,
        )

    if job.vehicle_restriction_status == "unclear":
        # Should already be caught by job.status above; kept as a safety net.
        return Decision(
            verdict=Verdict.HANDLE_MANUALLY, confidence=job.overall_confidence,
            reasons=[job.vehicle_restriction_reason], missing_information=missing,
            conflicts=conflicts, restrictions_triggered=restrictions_triggered, dispatch_group=dispatch_group,
        )

    vehicle_text = (job.vehicle.value or "").lower()
    service_text = (job.service_type.value or "").lower()
    for restriction in daily_rules.temporary_restrictions:
        r = restriction.lower()
        if r in vehicle_text or r in service_text:
            return Decision(
                verdict=Verdict.PASS, confidence=job.overall_confidence,
                reasons=[f"'{restriction}' is a temporary restriction today"], missing_information=missing,
                conflicts=conflicts, restrictions_triggered=restrictions_triggered + [restriction],
                dispatch_group=dispatch_group,
            )

    verdict = (
        Verdict.NEEDS_REVIEW if job.overall_confidence < NEEDS_REVIEW_CONFIDENCE_THRESHOLD else Verdict.ACCEPT
    )
    reason = (
        "area open, technician available, no restrictions triggered"
        if verdict == Verdict.ACCEPT
        else f"looks acceptable, but overall confidence ({job.overall_confidence}) is below the "
             f"high-confidence threshold ({NEEDS_REVIEW_CONFIDENCE_THRESHOLD}) -- double-check before approving"
    )
    quo_reply = "K" if profile.requires_k else None

    return Decision(
        verdict=verdict, confidence=job.overall_confidence, reasons=[reason],
        missing_information=missing, conflicts=conflicts, restrictions_triggered=restrictions_triggered,
        dispatch_group=dispatch_group, quo_reply_suggestion=quo_reply,
    )
