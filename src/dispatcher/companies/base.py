"""Shared shapes for per-company profiles, plus the shared closing template.

Each company (AGN, Lucky, Main Leads, and any future company) gets its own
profile module in this package. The closing format itself is shared across
companies by default (confirmed by the business owner on 2026-07-26 as the
same shape across AGN, Lucky, and other outside companies today) -- a
company only needs its own ClosingTemplate if its format is later found to
genuinely differ. See docs/phase1_scope.md, "Company profile architecture".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..models import Source


@dataclass(frozen=True)
class ClosingTemplate:
    template: str
    source_note: str


@dataclass(frozen=True)
class CompanyProfile:
    source: Source
    requires_k: bool
    closing_supported: bool
    closing_template: Optional[ClosingTemplate] = None
    # The company's own identifying header line (e.g. "Co: LUCKY LOCKSMITH LLC"),
    # slotted into the shared template's {header_line} placeholder. This is
    # company-specific *data*, not company-specific *logic* -- the template
    # itself stays shared.
    closing_header_line: Optional[str] = None
    unsupported_reason: Optional[str] = None


# The one shared closing shape: job fields repeated verbatim, then "Closed",
# then amount + payment method, then parts if any. Modeled on a real Lucky
# closing example and confirmed by the business owner to be the same shape
# used by AGN and other outside companies today.
SHARED_CLOSING_TEMPLATE = ClosingTemplate(
    template=(
        "{header_line}\n"
        "PDL: {ticket_number}\n"
        "Ph: {phone}\n"
        "Addr: {address}\n"
        "Desc: {service_type}\n"
        "Occu: Locksmith\n\n"
        "Notes: {vehicle}\n\n"
        "Closed\n"
        "{amount} {payment_method}\n"
        "{parts_line}"
    ),
    source_note=(
        "Shared default closing template, confirmed by the business owner "
        "on 2026-07-26 as used across AGN, Lucky, and other outside "
        "companies today. If a specific company's real closing turns out "
        "to differ, give that company its own ClosingTemplate in its "
        "profile module instead of editing this shared one."
    ),
)
