"""Converts an ExtractedJob into the Luxor Locksmith-branded technician
message.

Version 1 rule (confirmed by the business owner 2026-07-26): replace the
source company's branding with "Luxor Locksmith", preserve every important
field exactly as extracted, and only clean up spacing/line order -- never
summarize, rewrite, calculate, invent, or remove information.

This builds the message from already-extracted, verbatim fields (not by
regex-replacing the raw text) so a company name that happens to appear
inside a notes field, for example, is never accidentally mangled.
"""

from __future__ import annotations

from .models import ExtractedJob

NOT_PROVIDED = "(not provided)"


def format_technician_message(job: ExtractedJob) -> str:
    ticket = job.ticket_number.value or "UNKNOWN -- confirm ticket/PDL with dispatcher"
    name = job.customer_name.value or NOT_PROVIDED
    phone = job.phone.value or NOT_PROVIDED
    if job.phone.value and job.extension.value:
        phone = f"{job.phone.value} ext {job.extension.value}"
    # job.address.value is already the full raw address line (verbatim,
    # includes city/state/zip as originally written) -- use it as-is rather
    # than reassembling from the separately-parsed city/state/zip fields,
    # which would duplicate that text.
    address = job.address.value or NOT_PROVIDED
    service = job.service_type.value or NOT_PROVIDED
    vehicle = job.vehicle.value or "(not specified)"
    notes = job.notes.value or "(none)"

    lines = [
        "Luxor Locksmith",
        "",
        f"Ticket: {ticket}",
        f"Customer: {name}",
        f"Phone: {phone}",
        f"Address: {address}",
        f"Service: {service}",
        f"Vehicle: {vehicle}",
        f"Notes: {notes}",
    ]
    return "\n".join(lines)
