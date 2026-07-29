"""Canonical operational action-name literals, in one place.

Every one of these strings already existed scattered across storage.py,
luxor_desk_shared.py, and the luxor_desk_pages/*.py button handlers,
spelled independently at each call site. This module does not add a new
concept -- it gives the existing literals one authoritative source so
the application service (application.py) and the UI never spell them
independently again. derive_job_status(previous_status, message_type,
final_action) in storage.py remains the single status-derivation
authority; this module only names the final_action values that
authority already recognizes (or, for OPERATIONAL_NOTE, always
preserves status for).
"""

from __future__ import annotations

from enum import Enum


class ActionName(str, Enum):
    # Shared across NEW / STATUS / CLOSING / CANCELLATION message types --
    # derive_job_status()'s _APPROVE_LIKE_ACTIONS.
    APPROVE = "Approve"
    EDIT = "Edit"
    REJECT = "Reject"
    HANDLE_MANUALLY = "Handle Manually"

    # CANCELLATION-specific -- both confirm a dispatcher-initiated
    # pass/cancel; see derive_job_status()'s CANCELLATION branch.
    PASS = "Pass"
    CANCEL = "Cancel"

    # ASSIGNMENT-specific.
    ASSIGN = "Assign"
    REASSIGN = "Reassign"
    REMOVE = "Remove"

    # OPERATIONAL_NOTE-specific -- administrative confirmations that
    # always preserve the job's current status (see
    # derive_job_status()'s OPERATIONAL_NOTE branch).
    COMPANY_UPDATE_PREPARED = "Company Update Prepared"
    COMPANY_UPDATE_SENT_MANUALLY = "Company Update Sent Manually"
    COMPANY_CLOSING_PREPARED = "Company Closing Prepared"
    COMPANY_CLOSING_SENT_MANUALLY = "Company Closing Sent Manually"
    JOB_COMPLETED = "Job Completed"

    # Technician-roster configuration change (not job-scoped -- the
    # associated events row carries job_id=NULL). See
    # storage.update_technician_destinations().
    TECHNICIAN_CONFIGURATION_UPDATED = "Technician Configuration Updated"


# Technician update types -- dispatcher-selected, never inferred from
# free text (see application.py::record_technician_update and
# dispatcher.updates.reply_for_update_type). Centralized here so the UI
# (the selectbox options) and the storage-layer validation
# (storage._TECHNICIAN_UPDATE_TYPES) both read from one list instead of
# two independently-typed tuples.
TECHNICIAN_UPDATE_TYPES = (
    "Going", "ETA", "No Answer", "No Show", "Not Available",
    "Customer Cancelled", "Completed", "Custom",
)

# Job status literals -- storage.VALID_JOB_STATUSES remains the
# authoritative set; these names exist only so callers never spell a
# status string independently of that set.
STATUS_NEW = "New"
STATUS_NEEDS_REVIEW = "Needs Review"
STATUS_APPROVED = "Approved"
STATUS_HANDLE_MANUALLY = "Handle Manually"
STATUS_REJECTED = "Rejected"
STATUS_IN_PROGRESS = "In Progress"
STATUS_CLOSED = "Closed"
STATUS_CANCELLED = "Cancelled"

RECONCILIATION_SETTLED = "Settled"
