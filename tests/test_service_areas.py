from dispatcher.rules.service_areas import ServiceAreaRecord, find_service_area, load_service_areas


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
