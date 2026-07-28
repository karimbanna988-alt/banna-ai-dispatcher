"""The accept/pass/needs-review/handle-manually decision engine.

Combines, in order: the parser's own hard HANDLE_MANUALLY override,
permanent business hours (America/New_York, every day), the permanent
service-area mapping, each area's recurring weekly day off (if any), daily
technician-availability/restriction exceptions, and vehicle/service
restrictions. Never guesses -- every branch that can't confidently produce
ACCEPT or PASS falls back to HANDLE_MANUALLY. See docs/architecture.md
section 3 for the data contracts this was designed against, and
docs/workflow.md for the business rules confirmed 2026-07-27.
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
from .hours import check_business_hours
from .service_areas import ServiceAreaRecord, find_service_area, is_recurring_day_off

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
    # The confirmed service area's own label (e.g. "Charlotte") -- NOT a
    # WhatsApp dispatch group name. decide() has no way to know which
    # technician (and therefore which dispatch/closing group) will handle
    # this job; that's resolved later, by a human, at technician
    # assignment (storage.assign_technician). Renamed from the old
    # `dispatch_group` field 2026-07-28 so nothing here is ever mistaken
    # for routing information.
    service_area: Optional[str] = None
    quo_reply_suggestion: Optional[str] = None


def force_handle_manually(decision: Decision, reason: str) -> Decision:
    """Escalate an already-computed Decision to HANDLE_MANUALLY, prepending
    `reason`. For cross-cutting overrides decided outside decide()'s own
    checks -- e.g. the orchestration layer forces this when
    storage.classify_incoming() reports a closing arriving with no prior
    dispatch for its ticket (an anomaly that must never be treated as a
    normal accept/pass job)."""
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
    business_now: Optional[datetime] = None,
) -> Decision:
    """`now` (naive or aware) is only used for daily_rules.is_expired().
    `business_now` must be timezone-aware if provided (defaults to the real
    current time) and drives both the business-hours check and which
    weekday "today" is for recurring day-off matching -- always computed in
    America/New_York, never the operator's local clock."""
    missing, conflicts = _missing_and_conflicting(job)
    restrictions_triggered = (
        [job.vehicle_restriction_reason] if job.vehicle_restriction_status in ("restricted", "unclear") else []
    )

    def _decision(verdict: Verdict, reasons: List[str], service_area: Optional[str] = None,
                  quo_reply_suggestion: Optional[str] = None) -> Decision:
        return Decision(
            verdict=verdict, confidence=job.overall_confidence, reasons=reasons,
            missing_information=missing, conflicts=conflicts, restrictions_triggered=restrictions_triggered,
            service_area=service_area, quo_reply_suggestion=quo_reply_suggestion,
        )

    # 1. Hard override: the parser already determined this can't be trusted.
    if job.status == ExtractionStatus.HANDLE_MANUALLY:
        return _decision(Verdict.HANDLE_MANUALLY, list(job.status_reasons))

    # 2. Business hours: blanket check, before anything area-specific.
    hours = check_business_hours(business_now)
    if not hours.within_hours:
        return _decision(Verdict.HANDLE_MANUALLY, ["Outside operating hours", hours.reason])

    # 3. Permanent service-area lookup.
    area = find_service_area(job.city.value, job.state.value, service_areas)

    if area.status == "not_found":
        return _decision(Verdict.HANDLE_MANUALLY, [area.reason])
    if area.status == "inactive":
        return _decision(Verdict.PASS, [area.reason])

    # area.status == "confirmed" from here on. This is the area's own
    # label, never a WhatsApp group name -- decide() has no visibility
    # into technician-specific dispatch/closing groups (see Decision.service_area).
    service_area = area.record.city or area.record.state

    daily_active = daily_rules is not None and not daily_rules.is_expired(now)
    area_labels = {v for v in (job.city.value, job.state.value, area.record.city, area.record.state) if v}
    area_labels_lower = {a.lower() for a in area_labels}

    # 4. Recurring weekly day off -- a daily "open" exception can override it.
    opened_today = daily_active and any(o.lower() in area_labels_lower for o in daily_rules.open_areas)
    if is_recurring_day_off(area.record, hours.local_time) and not opened_today:
        reason = (
            f"{area.record.city or area.record.state} is on its recurring day off "
            f"({area.record.recurring_day_off})"
        )
        if area.record.recurring_day_off_action.upper() == "HANDLE_MANUALLY":
            return _decision(Verdict.HANDLE_MANUALLY, [reason], service_area=service_area)
        return _decision(Verdict.PASS, [reason], service_area=service_area)

    # 5. Daily exceptions (random, manually entered for the day) -- optional.
    tech_status = None
    if daily_active:
        if any(closed.lower() in area_labels_lower for closed in daily_rules.closed_areas):
            return _decision(
                Verdict.PASS, ["area marked temporarily unavailable today"], service_area=service_area,
            )
        for area_name, status in daily_rules.technician_status.items():
            if area_name.lower() in area_labels_lower:
                tech_status = status
                break

    effective_tech_status = tech_status or area.record.default_technician_availability

    if effective_tech_status in ("off", "busy"):
        return _decision(
            Verdict.PASS, [f"technician for this area is {effective_tech_status} today"], service_area=service_area,
        )
    if effective_tech_status == "unknown":
        return _decision(
            Verdict.HANDLE_MANUALLY,
            ["technician availability for this area is unknown and no confirmed backup exists"],
            service_area=service_area,
        )

    # 6. Vehicle restrictions.
    if job.vehicle_restriction_status == "restricted":
        return _decision(Verdict.PASS, [job.vehicle_restriction_reason], service_area=service_area)
    if job.vehicle_restriction_status == "unclear":
        # Should already be caught by job.status above; kept as a safety net.
        return _decision(Verdict.HANDLE_MANUALLY, [job.vehicle_restriction_reason], service_area=service_area)

    if daily_active:
        vehicle_text = (job.vehicle.value or "").lower()
        service_text = (job.service_type.value or "").lower()
        for restriction in daily_rules.temporary_restrictions:
            r = restriction.lower()
            if r in vehicle_text or r in service_text:
                restrictions_triggered.append(restriction)
                return _decision(
                    Verdict.PASS, [f"'{restriction}' is a temporary restriction today"], service_area=service_area,
                )

    # 7. Nothing excluded it -- accept or flag for a closer look on confidence.
    verdict = (
        Verdict.NEEDS_REVIEW if job.overall_confidence < NEEDS_REVIEW_CONFIDENCE_THRESHOLD else Verdict.ACCEPT
    )
    reason = (
        "area active, technician available, no restrictions triggered"
        if verdict == Verdict.ACCEPT
        else f"looks acceptable, but overall confidence ({job.overall_confidence}) is below the "
             f"high-confidence threshold ({NEEDS_REVIEW_CONFIDENCE_THRESHOLD}) -- double-check before approving"
    )
    quo_reply = "K" if profile.requires_k else None
    return _decision(verdict, [reason], service_area=service_area, quo_reply_suggestion=quo_reply)
