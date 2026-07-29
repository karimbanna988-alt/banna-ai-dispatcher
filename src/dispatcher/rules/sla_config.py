"""Speed Dispatch Console SLA thresholds -- kept here, not scattered
across UI files, so they're one place to tune. Purely display/urgency
classification; never affects extraction, classification, or the
decision engine."""

from __future__ import annotations

# A valid new job is flagged for attention immediately (age 0) -- these
# two thresholds escalate that flag as time passes without action.
ACKNOWLEDGMENT_WARNING_SECONDS = 60   # not acknowledged (K sent/prepared) by this age -> warning
DISPATCH_URGENT_SECONDS = 120         # not marked dispatched by this age -> urgent


def urgency_level(age_seconds: float, acknowledged: bool, dispatched: bool) -> str:
    """Returns "urgent" | "warning" | "attention" -- deterministic, based
    only on elapsed time and the two real recorded milestones. Never
    invents partial progress: a job with no acknowledgment recorded is
    always at least "attention" from the moment it's a valid job."""
    if dispatched:
        return "dispatched"
    if age_seconds >= DISPATCH_URGENT_SECONDS:
        return "urgent"
    if not acknowledged and age_seconds >= ACKNOWLEDGMENT_WARNING_SECONDS:
        return "warning"
    return "attention"


def format_age(age_seconds: float) -> str:
    """"18 seconds", "1 min 12 sec" -- never a raw ISO timestamp."""
    age_seconds = max(0, int(age_seconds))
    if age_seconds < 60:
        return f"{age_seconds} sec"
    minutes, seconds = divmod(age_seconds, 60)
    if seconds == 0:
        return f"{minutes} min"
    return f"{minutes} min {seconds} sec"
