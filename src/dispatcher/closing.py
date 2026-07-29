"""Renders a company-specific closing message from a confirmed technician report.

Never invents or recalculates an amount -- every value in ClosingInput must
come verbatim from what the technician actually reported. See
docs/workflow.md section 9 and docs/phase1_scope.md, "Company profile
architecture".
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional

from .companies import get_company_profile
from .models import ExtractedJob, Source


@dataclass
class ClosingInput:
    amount: str
    payment_method: str
    parts_amount: Optional[str] = None


@dataclass
class ClosingResult:
    supported: bool
    text: Optional[str]
    reason: str


def render_closing(job: ExtractedJob, closing: ClosingInput) -> ClosingResult:
    profile = get_company_profile(job.source)

    if not profile.closing_supported or profile.closing_template is None:
        return ClosingResult(
            supported=False,
            text=None,
            reason=profile.unsupported_reason
            or f"No closing template configured for {job.source.value}.",
        )

    parts_line = f"{closing.parts_amount} parts" if closing.parts_amount else ""
    text = (
        profile.closing_template.template.format(
            header_line=profile.closing_header_line or f"Co: {job.source.value}",
            ticket_number=job.ticket_number.value or "",
            phone=job.phone.value or "",
            address=job.address.value or "",
            service_type=job.service_type.value or "",
            vehicle=job.vehicle.value or "",
            amount=closing.amount,
            payment_method=closing.payment_method,
            parts_line=parts_line,
        ).rstrip()
        + "\n"
    )

    return ClosingResult(supported=True, text=text, reason=profile.closing_template.source_note)


def render_closing_from_persisted_fields(
    source: str, ticket_number: Optional[str], fields: dict, closing: ClosingInput,
) -> ClosingResult:
    """Same company-specific formatter as render_closing(), for a
    committed job read back from storage -- deliberately never
    reconstructs a full ExtractedJob. A committed job's persisted
    latest_fields_json has no raw_text/confidence/status_reasons (those
    only ever existed for the original in-memory extraction); inventing
    placeholder values for them just to build a fake ExtractedJob is
    exactly the fabrication this function avoids. It only reads the
    handful of plain, already-persisted field values the shared closing
    template actually needs (ticket_number/phone/address/service_type/
    vehicle) via a minimal SimpleNamespace view -- never a value that
    wasn't really stored."""
    view = SimpleNamespace(
        source=Source(source),
        ticket_number=SimpleNamespace(value=ticket_number),
        phone=SimpleNamespace(value=fields.get("phone")),
        address=SimpleNamespace(value=fields.get("address")),
        service_type=SimpleNamespace(value=fields.get("service_type")),
        vehicle=SimpleNamespace(value=fields.get("vehicle")),
    )
    return render_closing(view, closing)
