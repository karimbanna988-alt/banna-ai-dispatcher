"""Explicit, deterministic market-label -> canonical service-area
resolution for Main Leads.

Main Leads messages carry advertising/market labels ("Atlantic City
Locksmith Pro", "Locksmith Pros Savannah") that are NOT separate source
companies -- the source is always Main Leads (models.Source.MAIN_LEADS).
This module never invents an area from label text alone; a market label
is only ever a confirmed cross-check against the address-derived area,
never a replacement for it.

Resolution priority (see resolve_market_and_area()):
1. Address/city/state, already resolved via rules.service_areas.
   find_service_area() -- always tried first and always wins when
   present and confirmed.
2. An explicit, confirmed market-alias entry below -- used only when no
   usable address area exists, or to confirm agreement with one.
3. Neither resolves, or the two disagree -> Needs Review, never guessed.

No fuzzy matching anywhere in this module: market_alias_area() does an
exact (case-insensitive) lookup against a short, explicit allowlist.
The generic label "Locksmith Pros" is deliberately absent from that
allowlist -- it must always fall back to address-based resolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Only the market labels explicitly confirmed by the business owner.
# Never fuzzy-matched or substring-matched -- an unrecognized label
# resolves to None (not a best guess), and generic labels like
# "Locksmith Pros" are intentionally not listed here at all.
_MARKET_ALIASES = {
    "atlantic city locksmith pro": "NJ",
    "virginia beach locksmith": "Virginia Beach",
    "arlington locksmith pro": "Arlington",
    "locksmith pros savannah": "Savannah",
    "vancouver locksmith pro": "Vancouver",
}


def normalize_market_label(label: Optional[str]) -> str:
    return (label or "").strip().lower()


def market_alias_area(label: Optional[str]) -> Optional[str]:
    """Exact-match only against the confirmed allowlist above."""
    return _MARKET_ALIASES.get(normalize_market_label(label))


@dataclass(frozen=True)
class MarketResolution:
    status: str  # "resolved" | "needs_review"
    canonical_area: Optional[str]
    reason: str
    address_area: Optional[str] = None
    alias_area: Optional[str] = None


def resolve_market_and_area(address_area: Optional[str], market_label: Optional[str]) -> MarketResolution:
    """`address_area` is the canonical area already resolved from the
    job's real parsed address/city/state (via find_service_area) --
    always tried first. `market_label` is the raw advertising label
    text, if any, checked only as confirmation or fallback, never a
    substitute for a real address match."""
    alias_area = market_alias_area(market_label)

    if address_area and alias_area:
        if address_area.lower() == alias_area.lower():
            return MarketResolution(
                "resolved", address_area, "address and confirmed market alias agree",
                address_area=address_area, alias_area=alias_area,
            )
        return MarketResolution(
            "needs_review", None,
            f"address area {address_area!r} conflicts with confirmed market alias area {alias_area!r}",
            address_area=address_area, alias_area=alias_area,
        )

    if address_area:
        return MarketResolution("resolved", address_area, "resolved from parsed address", address_area=address_area)

    if alias_area:
        return MarketResolution(
            "resolved", alias_area, "resolved from confirmed market alias (no usable address)", alias_area=alias_area,
        )

    return MarketResolution("needs_review", None, "no usable address and no confirmed market alias")
