from ..models import Source
from .base import SHARED_CLOSING_TEMPLATE, CompanyProfile

# Per the business owner (2026-07-26), AGN's closing format follows the same
# shared shape as Lucky's -- only the header line differs (AGN's own Quo
# job header, "Ref: AGN Locksmith Services"). If a real AGN closing example
# later shows a genuinely different format, give AGN its own ClosingTemplate
# here instead of using SHARED_CLOSING_TEMPLATE.
PROFILE = CompanyProfile(
    source=Source.AGN,
    requires_k=True,
    closing_supported=True,
    closing_template=SHARED_CLOSING_TEMPLATE,
    closing_header_line="Ref: AGN Locksmith Services",
)
