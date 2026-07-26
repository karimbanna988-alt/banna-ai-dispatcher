from ..models import Source
from .base import CompanyProfile

PROFILE = CompanyProfile(
    source=Source.MAIN_LEADS,
    requires_k=False,
    closing_supported=False,
    unsupported_reason=(
        "Main Leads jobs are our own advertising leads -- they are dispatched "
        "directly and never require a closing sent back to a company."
    ),
)
