"""Source detection and field extraction for raw Quo job messages.

Never invents a value: every FieldValue is either something matched
directly in the text, or explicitly marked missing/unclear with a reason.
See docs/workflow.md and docs/company_templates.md for the business rules
this implements.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

from .models import ExtractedJob, ExtractionStatus, FieldValue, Source

RULES_PATH = Path(__file__).resolve().parents[2] / "rules" / "vehicle_restrictions.json"

REQUIRED_FIELDS = ("ticket_number", "phone", "address", "service_type")
OVERALL_CONFIDENCE_THRESHOLD = 50

MAKES = [
    "Toyota", "Honda", "Ford", "Chevrolet", "Chevy", "Nissan", "Hyundai",
    "Kia", "Jeep", "Dodge", "Chrysler", "GMC", "Mazda", "Subaru", "Lexus",
    "Acura", "Infiniti", "Cadillac", "Buick", "Lincoln", "Ram", "Tesla",
    "Volvo", "Mitsubishi", "Mercedes-Benz", "Mercedes", "BMW", "Audi",
    "Volkswagen", "VW", "Pontiac", "Saturn", "Scion", "Mini",
]
_MAKES_PATTERN = "|".join(re.escape(m) for m in MAKES)

# NOTE: these use [ \t]* rather than \s* right after the label — \s* also
# matches newlines, which would let an empty field silently swallow the
# line break and bleed into the next labeled line.
TICKET_RE = re.compile(r"^(?:Ticket|PDL):[ \t]*(?P<val>\S+)", re.MULTILINE)
# AGN sometimes provides a customer name via a bare "N:" label (confirmed
# 2026-07-27 from a real AGN job) even though earlier samples never had one
# -- don't assume a source structurally lacks a name field just because one
# past example omitted it.
NAME_RE = re.compile(r"^(?:Name|N):[ \t]*(?P<val>.*)$", re.MULTILINE)
PHONE_RE = re.compile(
    r"^(?:Phone|Ph):[ \t]*(?P<phone>[\d()+\-. ]*\d)?[ \t]*(?:\(Job Ext:[ \t]*(?P<ext>\d+)\))?[ \t]*$",
    re.MULTILINE,
)
# A genuine second contact number some Main Leads jobs provide -- must be
# kept, never dropped, when the customer's primary line doesn't answer.
SECOND_PHONE_RE = re.compile(
    r"^2nd Phone\s*#?:[ \t]*(?P<phone>[\d()+\-. ]*\d)?[ \t]*$", re.MULTILINE,
)
DESC_RE = re.compile(r"^Desc:[ \t]*(?P<val>.*)$", re.MULTILINE)
OCCU_RE = re.compile(r"^Occu:[ \t]*.*$", re.MULTILINE)
ADDRESS_LINE_RE = re.compile(r"^(?:Address|Addr):[ \t]*(?P<line>.*)$", re.MULTILINE)
ADDRESS_TAIL_RE = re.compile(
    r"(?P<city>[A-Za-z][A-Za-z .'-]*?),\s*(?P<state>[A-Z]{2})\s*,?\s*(?P<zip>\d{5})?\s*$"
)
VEHICLE_RE = re.compile(
    rf"\b(?:(?P<year1>(?:19|20)\d{{2}})\s+)?(?P<make>{_MAKES_PATTERN})\b"
    rf"\s*(?P<model>[A-Za-z0-9]+)?\s*(?:(?P<year2>(?:19|20)\d{{2}})|(?P<year2_2d>\d{{2}})\b)?",
    re.IGNORECASE,
)


def load_vehicle_rules(path: Optional[Path] = None) -> dict:
    p = path or RULES_PATH
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def detect_source(raw_text: str) -> Tuple[Source, int, str]:
    if re.search(r"Power By Ucallz App", raw_text, re.IGNORECASE) or re.search(
        r"^Ticket:", raw_text, re.MULTILINE
    ):
        return Source.MAIN_LEADS, 95, 'matched "Power By Ucallz App" header / Ticket: field'
    if re.search(r"Ref:\s*AGN Locksmith Services", raw_text, re.IGNORECASE):
        return Source.AGN, 95, 'matched "Ref: AGN Locksmith Services" header'
    if re.search(r"Co:\s*LUCKY LOCKSMITH", raw_text, re.IGNORECASE):
        return Source.LUCKY, 95, 'matched "Co: LUCKY LOCKSMITH LLC" header'
    if re.search(r"^PDL:", raw_text, re.MULTILINE):
        return (
            Source.UNKNOWN,
            40,
            "found a PDL: field but no known company header (AGN/Lucky) matched"
            " — possibly a new company template",
        )
    return Source.UNKNOWN, 10, "no known template fingerprint matched"


def extract_ticket(raw_text: str) -> FieldValue:
    matches = TICKET_RE.findall(raw_text)
    if not matches:
        return FieldValue(None, 0, "no Ticket:/PDL: field found")
    unique = list(dict.fromkeys(matches))
    if len(unique) > 1:
        return FieldValue(
            unique[0], 30,
            "multiple different ticket/PDL values found in message",
            candidates=unique,
        )
    return FieldValue(unique[0], 95, "parsed from Ticket:/PDL: field")


def extract_name(raw_text: str) -> FieldValue:
    m = NAME_RE.search(raw_text)
    if not m:
        return FieldValue(None, 0, "no Name: field found")
    val = m.group("val").strip()
    if not val:
        return FieldValue(None, 0, "Name: field present but empty")
    return FieldValue(val, 90, "parsed from Name: field")


def _split_bare_trailing_extension(phone_raw: str) -> Tuple[str, Optional[str]]:
    """Some jobs give the extension as a bare trailing number with no
    "(Job Ext: N)" wrapper, e.g. "7048000948 133" (confirmed 2026-07-27,
    a real Lucky job). Only split when the part before the last space is
    a plausible complete phone number (>=10 digits) and the trailing part
    is short and purely numeric -- otherwise a normally-spaced phone
    number (e.g. "704 800 0948") could be wrongly split."""
    if " " not in phone_raw:
        return phone_raw, None
    main, _, trailing = phone_raw.rpartition(" ")
    digits_in_main = re.sub(r"\D", "", main)
    if trailing.isdigit() and 1 <= len(trailing) <= 5 and len(digits_in_main) >= 10:
        return main.strip(), trailing
    return phone_raw, None


def extract_phone_and_extension(raw_text: str) -> Tuple[FieldValue, FieldValue]:
    m = PHONE_RE.search(raw_text)
    if not m or not m.group("phone"):
        missing = FieldValue(None, 0, "no Phone:/Ph: field with a number found")
        return missing, FieldValue(None, 0, "no extension present")

    ext_val = m.group("ext")
    phone_raw = m.group("phone").strip()

    if ext_val:
        phone_field = FieldValue(phone_raw, 95, "parsed from Phone:/Ph: field")
        ext_field = FieldValue(ext_val, 95, "parsed from (Job Ext: N) in the phone line")
        return phone_field, ext_field

    main_phone, bare_ext = _split_bare_trailing_extension(phone_raw)
    if bare_ext:
        phone_field = FieldValue(main_phone, 90, "parsed from Phone:/Ph: field, extension split off")
        ext_field = FieldValue(bare_ext, 75, "parsed as a bare trailing extension after the phone number")
        return phone_field, ext_field

    phone_field = FieldValue(phone_raw, 95, "parsed from Phone:/Ph: field")
    ext_field = FieldValue(None, 100, "no extension provided in the message — not invented")
    return phone_field, ext_field


def extract_second_phone(raw_text: str) -> FieldValue:
    m = SECOND_PHONE_RE.search(raw_text)
    if not m or not m.group("phone"):
        return FieldValue(None, 0, "no 2nd Phone #: field with a number found")
    return FieldValue(m.group("phone").strip(), 95, "parsed from 2nd Phone #: field")


def extract_address(raw_text: str) -> Dict[str, FieldValue]:
    matches = ADDRESS_LINE_RE.findall(raw_text)
    if not matches or not matches[0].strip():
        reason = "no Address:/Addr: field found" if not matches else "Address:/Addr: field present but empty"
        missing = FieldValue(None, 0, reason)
        return {"address": missing, "city": missing, "state": missing, "zip_code": missing}

    line = matches[0].strip()
    address_field = FieldValue(line, 95, "raw address line from Address:/Addr: field")

    m = ADDRESS_TAIL_RE.search(line)
    if not m:
        no_parse_reason = "could not parse city/state/zip out of the address line"
        return {
            "address": address_field,
            "city": FieldValue(None, 0, no_parse_reason),
            "state": FieldValue(None, 0, no_parse_reason),
            "zip_code": FieldValue(None, 0, no_parse_reason),
        }

    city = m.group("city").strip().rstrip(",").strip() or None
    state = m.group("state")
    zip_code = m.group("zip")

    return {
        "address": address_field,
        "city": FieldValue(city, 90, "parsed from address line"),
        "state": FieldValue(state, 90, "parsed from address line"),
        "zip_code": (
            FieldValue(zip_code, 85, "parsed from address line")
            if zip_code else FieldValue(None, 0, "no zip found in address line")
        ),
    }


def extract_service_type(raw_text: str) -> FieldValue:
    m = DESC_RE.search(raw_text)
    if not m:
        return FieldValue(None, 0, "no Desc: field found")
    val = re.sub(r"\s*\[[^\]]*\]\s*$", "", m.group("val").strip()).strip()
    if not val:
        return FieldValue(None, 0, "Desc: field present but empty")
    return FieldValue(val, 90, "parsed from Desc: field")


def year_from_two_digits(two_digits: str) -> int:
    """Common vehicle-year shorthand convention: 00-69 -> 20XX, 70-99 -> 19XX
    (confirmed useful 2026-07-27 from a real "honda accord 12" job -- means
    a 2012 Accord). Locksmith car-key jobs essentially never involve
    pre-1995 vehicles (transponder keys didn't exist yet), so this pivot is
    a standard, safe interpretation of shorthand notation, not a guess of
    an otherwise-unknown fact."""
    n = int(two_digits)
    return 2000 + n if n <= 69 else 1900 + n


def extract_vehicle(raw_text: str) -> FieldValue:
    m = VEHICLE_RE.search(raw_text)
    if not m:
        return FieldValue(None, 0, "no vehicle make recognized in message")

    year1, year2, year2_2d, model = m.group("year1"), m.group("year2"), m.group("year2_2d"), m.group("model")
    if not year1 and not year2 and not year2_2d and model and model.isdigit():
        if len(model) == 4 and model[:2] in ("19", "20"):
            year2 = model
        elif len(model) == 2:
            year2_2d = model

    value = m.group(0).strip()
    if year1 or year2:
        return FieldValue(value, 85, "vehicle make/model/year matched directly in message text")
    if year2_2d:
        interpreted = year_from_two_digits(year2_2d)
        return FieldValue(
            value, 78,
            f"vehicle year inferred from 2-digit shorthand '{year2_2d}' as {interpreted} "
            "(convention: 00-69 -> 20XX, 70-99 -> 19XX)",
        )
    return FieldValue(value, 55, "vehicle make found but no model year nearby — treat with caution")


def check_vehicle_restriction(vehicle_text: Optional[str], rules: dict) -> Tuple[str, str]:
    """Returns (status, reason). status is one of:
    not_applicable, allowed, restricted, unclear.
    """
    if not vehicle_text:
        return "not_applicable", "no vehicle info to check"

    text_lower = vehicle_text.lower()

    for make in rules.get("excluded_makes", []):
        if make.lower() in text_lower:
            return "restricted", f"{make} is an excluded make"

    year_match = re.search(r"(?:19|20)\d{2}", vehicle_text)
    if year_match:
        year = int(year_match.group())
    else:
        two_digit_match = re.search(r"\b(\d{2})\b", vehicle_text)
        year = year_from_two_digits(two_digit_match.group(1)) if two_digit_match else None

    for make, cap_year in rules.get("make_year_caps", {}).items():
        if make.lower() in text_lower:
            if year is None:
                return "unclear", f"{make} requires a model year to check the {cap_year} cutoff, but no year was found"
            if year <= cap_year:
                return "allowed", f"{make} {year} is within the accepted range (up to {cap_year})"
            return "restricted", f"{make} {year} exceeds the accepted range (up to {cap_year})"

    oldest = rules.get("oldest_model_year_allowed")
    if oldest is not None:
        if year is None:
            return "unclear", "no model year found to check against the oldest-accepted-year rule"
        if year < oldest:
            return "restricted", f"model year {year} is older than the oldest accepted year ({oldest})"

    return "allowed", "no matching restriction found for this vehicle"


_AGN_LUCKY_FIELD_PATTERNS = (TICKET_RE, NAME_RE, PHONE_RE, ADDRESS_LINE_RE, DESC_RE, OCCU_RE)


def _last_recognized_field_end(raw_text: str) -> Optional[int]:
    """End position of whichever known AGN/Lucky field label occurs latest
    in the message -- used to anchor trailing content when there's no
    "Notes:" label to anchor on instead."""
    end = None
    for pattern in _AGN_LUCKY_FIELD_PATTERNS:
        matches = list(pattern.finditer(raw_text))
        if matches and (end is None or matches[-1].end() > end):
            end = matches[-1].end()
    return end


def extract_notes(raw_text: str, source: Source) -> FieldValue:
    if source in (Source.AGN, Source.LUCKY):
        # Quo sometimes repeats an empty "Notes:" label before the one that
        # actually carries content (confirmed 2026-07-27, a real Lucky job:
        # "Notes:\n\nNotes:\nCX IS AT BEST BUY") -- use the LAST occurrence
        # so the real content isn't preceded by a redundant empty label.
        # (a single greedy/DOTALL "Notes:.*" pattern would consume to the
        # end of the string on its first match, so finding all *label*
        # positions first is necessary to locate the last one.)
        label_positions = list(re.finditer(r"Notes:", raw_text))
        if label_positions:
            val = raw_text[label_positions[-1].end():].strip()
            if not val:
                return FieldValue(None, 0, "Notes: field present but empty")
            return FieldValue(val, 85, "parsed verbatim from the last Notes: label in the message")

        # No "Notes:" label at all -- confirmed 2026-07-27 by a real AGN job
        # that ended in a bare "K?" with no label whatsoever. Without a
        # fallback, that content was silently dropped: not in any field, not
        # anywhere. Anchor on whichever recognized field (PDL/N/Ph/Addr/
        # Desc/Occu) occurs last in the message and capture anything after
        # it, so trailing content is never lost just because Quo omitted
        # the "Notes:" label this time.
        anchor = _last_recognized_field_end(raw_text)
        if anchor is None:
            return FieldValue(None, 0, "no Notes: field or other recognized field found to anchor trailing notes")
        tail = raw_text[anchor:].strip()
        if not tail:
            return FieldValue(None, 0, "no Notes: field found, and no trailing content after the last recognized field")
        return FieldValue(tail, 70, "no Notes: label found -- captured trailing content after the last recognized field")

    # Main Leads doesn't reliably separate the freeform vehicle/notes tail
    # from the structured fields with a blank line -- confirmed 2026-07-27
    # by a real job with zero blank lines after "Desc:" at all (an earlier
    # blank-line-block-splitting heuristic wrongly swallowed the entire
    # message -- ticket, name, phone, address -- into "notes" here). The
    # one thing that's consistent across every real sample seen is that
    # "Desc:" is followed immediately by the vehicle/notes content, so
    # anchor on it directly instead.
    desc_matches = list(DESC_RE.finditer(raw_text))
    if not desc_matches:
        return FieldValue(None, 0, "no Desc: field found to anchor trailing notes")
    tail = raw_text[desc_matches[-1].end():].strip()
    if not tail:
        return FieldValue(None, 0, "no content after Desc: field")
    return FieldValue(tail, 75, "everything after Desc: taken verbatim (may include vehicle info and freeform notes)")


def _compute_status(job: ExtractedJob) -> None:
    reasons = []
    fields = job.all_fields()

    for name in REQUIRED_FIELDS:
        fv = fields[name]
        if fv.is_missing:
            reasons.append(f"required field '{name}' is missing")
        elif fv.is_conflicting:
            reasons.append(f"required field '{name}' has conflicting candidate values: {fv.candidates}")

    for name, fv in fields.items():
        if name not in REQUIRED_FIELDS and fv.is_conflicting:
            reasons.append(f"field '{name}' has conflicting candidate values: {fv.candidates}")

    if job.source == Source.UNKNOWN:
        reasons.append("source company could not be confidently identified")

    if job.vehicle_restriction_status == "unclear":
        reasons.append(job.vehicle_restriction_reason)

    confidences = [fields[name].confidence for name in REQUIRED_FIELDS]
    confidences.append(job.source_confidence)
    job.overall_confidence = min(confidences) if confidences else 0

    if job.overall_confidence < OVERALL_CONFIDENCE_THRESHOLD:
        reasons.append(
            f"overall confidence {job.overall_confidence} is below the review threshold"
            f" ({OVERALL_CONFIDENCE_THRESHOLD})"
        )

    if reasons:
        job.status = ExtractionStatus.HANDLE_MANUALLY
        job.status_reasons = reasons
    else:
        job.status = ExtractionStatus.OK
        job.status_reasons = []


def extract_job(raw_text: str, rules: Optional[dict] = None) -> ExtractedJob:
    rules = rules if rules is not None else load_vehicle_rules()

    source, source_confidence, source_reason = detect_source(raw_text)
    ticket = extract_ticket(raw_text)
    # Attempt name extraction regardless of source -- AGN and Lucky don't
    # always include one, but AGN has been seen to provide it via "N:"
    # (2026-07-27), so never assume a source structurally lacks a field
    # just because one past example omitted it.
    name = extract_name(raw_text)
    phone, extension = extract_phone_and_extension(raw_text)
    second_phone = extract_second_phone(raw_text)
    addr = extract_address(raw_text)
    service_type = extract_service_type(raw_text)
    vehicle = extract_vehicle(raw_text)
    notes = extract_notes(raw_text, source)

    job = ExtractedJob(
        raw_text=raw_text,
        source=source,
        source_confidence=source_confidence,
        source_reason=source_reason,
        ticket_number=ticket,
        customer_name=name,
        phone=phone,
        second_phone=second_phone,
        extension=extension,
        address=addr["address"],
        city=addr["city"],
        state=addr["state"],
        zip_code=addr["zip_code"],
        service_type=service_type,
        vehicle=vehicle,
        notes=notes,
    )

    vehicle_status, vehicle_reason = check_vehicle_restriction(vehicle.value, rules)
    job.vehicle_restriction_status = vehicle_status
    job.vehicle_restriction_reason = vehicle_reason

    _compute_status(job)
    return job
