from ..models import Source
from .base import SHARED_CLOSING_TEMPLATE, CompanyProfile

PROFILE = CompanyProfile(
    source=Source.LUCKY,
    requires_k=True,
    closing_supported=True,
    closing_template=SHARED_CLOSING_TEMPLATE,
    closing_header_line="Co: LUCKY LOCKSMITH LLC",
)
