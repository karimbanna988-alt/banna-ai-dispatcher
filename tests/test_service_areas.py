from dispatcher.rules.service_areas import (
    ServiceAreaRecord,
    find_service_area,
    load_service_areas,
    resolve_area_text,
)


def test_loads_real_service_areas_file():
    areas = load_service_areas()
    assert any(a.city == "Charlotte" and a.state == "NC" for a in areas)
    assert any(a.city is None and a.state == "DE" for a in areas)


def test_known_active_area_is_confirmed_without_any_group_data():
    """2026-07-28: dispatch/closing group names were removed from service
    areas entirely (they're now opaque, technician-owned data -- see
    storage.py's technicians table). A known, active area is "confirmed"
    as soon as it's found -- there is no more "unconfirmed" status."""
    areas = load_service_areas()
    lookup = find_service_area("Charlotte", "NC", areas)
    assert lookup.status == "confirmed"
    assert lookup.record.city == "Charlotte"


def test_unknown_city_is_not_found():
    areas = load_service_areas()
    lookup = find_service_area("Nowhereville", "ZZ", areas)
    assert lookup.status == "not_found"


def test_whole_state_entry_matches_on_state_alone():
    areas = load_service_areas()
    lookup = find_service_area(None, "DE", areas)
    assert lookup.status == "confirmed"
    assert lookup.record is not None
    assert lookup.record.state == "DE"


def test_inactive_area_is_reported_as_inactive():
    areas = [ServiceAreaRecord(city="Testville", state="TS", active=False)]
    lookup = find_service_area("Testville", "TS", areas)
    assert lookup.status == "inactive"


def test_active_area_is_confirmed():
    areas = [ServiceAreaRecord(city="Testville", state="TS")]
    lookup = find_service_area("Testville", "TS", areas)
    assert lookup.status == "confirmed"
    assert lookup.record.city == "Testville"


# ------------------------------------------------------- resolve_area_text
def _synthetic_areas():
    return [
        ServiceAreaRecord(city="Faketon", state="FS"),
        ServiceAreaRecord(city=None, state="NJ"),  # whole-state entry, mirrors the real New Jersey record
    ]


def test_resolve_area_text_city_and_full_state_name():
    lookup = resolve_area_text("Faketon, Fake State", _synthetic_areas())
    # "Fake State" isn't in the abbreviation table, so this falls back to
    # city-only matching -- still resolves correctly since Faketon is
    # unambiguous in this synthetic set.
    assert lookup.status == "confirmed"
    assert lookup.record.city == "Faketon"


def test_resolve_area_text_bare_full_state_name():
    lookup = resolve_area_text("New Jersey", _synthetic_areas())
    assert lookup.status == "confirmed"
    assert lookup.record.state == "NJ"
    assert lookup.record.city is None  # whole-state entry, area label will be "NJ" not a city


def test_resolve_area_text_typo_correction():
    lookup = resolve_area_text("raliegh", load_service_areas())
    assert lookup.status == "confirmed"
    assert lookup.record.city == "Raleigh"


def test_resolve_area_text_rejects_unconfigured_place_without_inventing_it():
    """"Atlanta" is not in rules/service_areas.yaml -- must resolve as
    not_found, never silently matched to something else or added."""
    lookup = resolve_area_text("Atlanta", load_service_areas())
    assert lookup.status == "not_found"


def test_resolve_area_text_empty_input_is_not_found():
    lookup = resolve_area_text("", _synthetic_areas())
    assert lookup.status == "not_found"
