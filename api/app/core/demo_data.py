from datetime import date

DECLINE_RATE_BBL_PER_DAY = 0.5

WELLS = [
    {"id": "POZO-001", "base_production": 200.0, "active_from": date(2020, 1, 1)},
    {"id": "POZO-002", "base_production": 150.0, "active_from": date(2021, 6, 1)},
    {"id": "POZO-003", "base_production": 100.0, "active_from": date(2022, 1, 1)},
]


def get_well(id_well: str) -> dict | None:
    """Return the well record with the given id, or None if it does not exist."""
    return next((w for w in WELLS if w["id"] == id_well), None)


def well_exists(id_well: str) -> bool:
    return get_well(id_well) is not None
