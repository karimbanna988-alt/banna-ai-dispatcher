"""Converts an ExtractedJob into the canonical LUXOR LOCKSMITH technician
message.

Canonical format (v2, confirmed by the business owner 2026-07-27, replacing
the earlier V1 label-based layout): a purpose-built structure that looks
clearly different from Main Leads/AGN/Lucky's own templates, not a copy of
any source company's labels. Extension stays inline with Phone -- it isn't
in the business owner's template spec, but dropping it would violate the
standing "never remove the extension" rule from the requirements
interview, so it's kept per the owner's confirmation. Optional fields
(Customer, Alternate Phone, Vehicle, Job Notes) are omitted entirely when
absent; core fields (Ticket, Phone, Service Address, Service Requested)
show a placeholder instead, since those being missing is itself worth
flagging rather than hiding. Job Notes is shown exactly as extracted, even
when it duplicates the Vehicle line (confirmed by the business owner as
acceptable -- notes are never trimmed or deduplicated to avoid any risk of
summarizing/rewriting real content).

This builds the message from already-extracted, verbatim fields (not by
regex-replacing the raw text) so a company name that happens to appear
inside a notes field, for example, is never accidentally mangled. Never
branches on job.status or a Decision -- HANDLE_MANUALLY is an internal
decision-layer concern, not a different technician-message template.
"""

from __future__ import annotations

from .models import ExtractedJob

NOT_PROVIDED = "(not provided)"


def format_technician_message(job: ExtractedJob) -> str:
    phone = job.phone.value or NOT_PROVIDED
    if job.phone.value and job.extension.value:
        phone = f"{job.phone.value} ext {job.extension.value}"

    lines = ["LUXOR LOCKSMITH", "NEW SERVICE REQUEST", ""]
    lines.append(f"Ticket: {job.ticket_number.value or NOT_PROVIDED}")
    if job.customer_name.value:
        lines.append(f"Customer: {job.customer_name.value}")
    lines.append(f"Phone: {phone}")
    if job.second_phone.value:
        lines.append(f"Alternate Phone: {job.second_phone.value}")

    # job.address.value is already the full raw address line (verbatim,
    # includes city/state/zip as originally written) -- use it as-is rather
    # than reassembling from the separately-parsed city/state/zip fields,
    # which would duplicate that text.
    lines += ["", "Service Address:", job.address.value or NOT_PROVIDED]
    lines += ["", "Service Requested:", job.service_type.value or NOT_PROVIDED]

    if job.vehicle.value:
        lines += ["", "Vehicle:", job.vehicle.value]

    if job.notes.value:
        lines += ["", "Job Notes:", job.notes.value]

    return "\n".join(lines)
