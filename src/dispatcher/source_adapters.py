"""Source adapter boundaries for AGN, Lucky, Main Leads, and Lucky's
"Closings & Replies" reply channel.

This is a read-only facade over two already-existing, already-
deterministic tables -- dispatcher.companies (per-company closing
template/support) and dispatcher.policy (per-stream, per-message-type
acknowledgment/formatting/closing policy) -- composed into one object
per source so callers ask one adapter instead of consulting two
separate registries and re-deriving the relationship themselves. It
does not parse, classify, or format anything itself: parsing stays in
parser.py, classification stays in storage.classify_incoming(),
technician-message formatting stays in formatter.py, closing formatting
stays in closing.py. One shared pipeline, four small policy adapters --
never four duplicate pipelines.

"Lucky -- Closings & Replies" is a STREAM (a Quo contact polled), not a
Source company -- see policy.LUCKY_CLOSINGS_STREAM_KEY and
luxor_ops_shared.STREAMS. It remains Lucky's own operational reply
thread here, keyed separately, never treated as a fifth company.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .companies import get_company_profile
from .models import Source
from .policy import LUCKY_CLOSINGS_STREAM_KEY, ResponsePolicy, get_policy
from .storage import MessageType


@dataclass(frozen=True)
class SourceAdapter:
    """One source's (or stream's) full deterministic policy surface,
    composed from companies.py + policy.py -- never a third,
    independently-maintained copy of either."""

    stream_key: str
    source: Optional[Source]  # None for the Closings & Replies stream
    requires_k: bool
    closing_supported: bool
    closing_unsupported_reason: Optional[str]

    def acknowledgment_policy(self, message_type: str) -> ResponsePolicy:
        """Ack requirement/text/technician-formatting/closing-handling
        for one (stream, message_type) combination -- delegates entirely
        to policy.get_policy(), never re-derives it."""
        return get_policy(self.stream_key, message_type)

    def acknowledgment_required_for_new_job(self) -> bool:
        return self.acknowledgment_policy(MessageType.NEW.value).acknowledgment_required

    def acknowledgment_text_for_new_job(self) -> Optional[str]:
        return self.acknowledgment_policy(MessageType.NEW.value).acknowledgment_text


# One adapter per real Source, plus the Closings & Replies stream --
# matches luxor_ops_shared.STREAMS' own four-entry list exactly (three
# companies + one reply channel), not a fifth invented company.
_AGN_PROFILE = get_company_profile(Source.AGN)
_LUCKY_PROFILE = get_company_profile(Source.LUCKY)
_MAIN_LEADS_PROFILE = get_company_profile(Source.MAIN_LEADS)

AGN_ADAPTER = SourceAdapter(
    stream_key=Source.AGN.value, source=Source.AGN,
    requires_k=_AGN_PROFILE.requires_k, closing_supported=_AGN_PROFILE.closing_supported,
    closing_unsupported_reason=_AGN_PROFILE.unsupported_reason,
)
LUCKY_ADAPTER = SourceAdapter(
    stream_key=Source.LUCKY.value, source=Source.LUCKY,
    requires_k=_LUCKY_PROFILE.requires_k, closing_supported=_LUCKY_PROFILE.closing_supported,
    closing_unsupported_reason=_LUCKY_PROFILE.unsupported_reason,
)
MAIN_LEADS_ADAPTER = SourceAdapter(
    stream_key=Source.MAIN_LEADS.value, source=Source.MAIN_LEADS,
    requires_k=_MAIN_LEADS_PROFILE.requires_k, closing_supported=_MAIN_LEADS_PROFILE.closing_supported,
    closing_unsupported_reason=_MAIN_LEADS_PROFILE.unsupported_reason,
)
LUCKY_CLOSINGS_REPLIES_ADAPTER = SourceAdapter(
    stream_key=LUCKY_CLOSINGS_STREAM_KEY, source=None,
    requires_k=False, closing_supported=False,
    closing_unsupported_reason=(
        "Lucky -- Closings & Replies is an operational reply channel, not a new-job "
        "acknowledgment or closing-template channel -- confirmed closings on this thread "
        "are recorded against the correlated job, using that job's own source adapter."
    ),
)

_ADAPTERS_BY_STREAM_KEY = {
    Source.AGN.value: AGN_ADAPTER,
    Source.LUCKY.value: LUCKY_ADAPTER,
    Source.MAIN_LEADS.value: MAIN_LEADS_ADAPTER,
    LUCKY_CLOSINGS_STREAM_KEY: LUCKY_CLOSINGS_REPLIES_ADAPTER,
}


def get_source_adapter(stream_key: str) -> SourceAdapter:
    """`stream_key` is a Source value (AGN/Lucky/Main Leads) or
    LUCKY_CLOSINGS_STREAM_KEY -- never a raw Quo group_label. An unknown
    key gets a safe, fully-locked-down adapter (no ack, no closing)
    rather than raising -- matching policy.SAFE_DEFAULT_POLICY's own
    fail-safe philosophy."""
    return _ADAPTERS_BY_STREAM_KEY.get(
        stream_key,
        SourceAdapter(
            stream_key=stream_key, source=None, requires_k=False, closing_supported=False,
            closing_unsupported_reason=f"No source adapter configured for {stream_key!r}.",
        ),
    )


def get_source_adapter_for_source(source_value: str) -> SourceAdapter:
    """Convenience for the common case of already having a
    dispatcher.models.Source value (e.g. from a committed job's `source`
    column) rather than a stream_key."""
    return get_source_adapter(source_value)
