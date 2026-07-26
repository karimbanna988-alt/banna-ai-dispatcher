"""Registry of per-company profiles.

To add or change a company's workflow, add/edit its own module in this
package -- never edit another company's module to do it. See
docs/phase1_scope.md, "Company profile architecture".
"""

from __future__ import annotations

from ..models import Source
from . import agn, lucky, main_leads
from .base import ClosingTemplate, CompanyProfile

UNKNOWN_PROFILE = CompanyProfile(
    source=Source.UNKNOWN,
    requires_k=False,
    closing_supported=False,
    unsupported_reason="Source company was not identified -- must be handled manually.",
)

_REGISTRY = {
    Source.MAIN_LEADS: main_leads.PROFILE,
    Source.AGN: agn.PROFILE,
    Source.LUCKY: lucky.PROFILE,
    Source.UNKNOWN: UNKNOWN_PROFILE,
}


def get_company_profile(source: Source) -> CompanyProfile:
    try:
        return _REGISTRY[source]
    except KeyError as exc:
        raise KeyError(f"No company profile registered for source: {source}") from exc


__all__ = ["get_company_profile", "CompanyProfile", "ClosingTemplate"]
