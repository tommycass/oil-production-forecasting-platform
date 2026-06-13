from datetime import date

DECLINE_RATE_BBL_PER_DAY = 0.5

# Base_production de fallback para IDs reales del DW (idpozo numérico) que no
# están en la lista de demo. El modelo de declive lineal es sintético de todas
# formas; lo único que varía es el nivel inicial. Valor elegido como promedio
# representativo de la flota de demo.
_DEFAULT_BASE_PRODUCTION = 200.0

WELLS = [
    {"id": "POZO-001", "base_production": 200.0, "active_from": date(2020, 1, 1)},
    {"id": "POZO-002", "base_production": 150.0, "active_from": date(2021, 6, 1)},
    {"id": "POZO-003", "base_production": 100.0, "active_from": date(2022, 1, 1)},
    {"id": "POZO-004", "base_production": 350.0, "active_from": date(2015, 3, 15)},
    {"id": "POZO-005", "base_production": 80.0, "active_from": date(2023, 7, 1)},
    {"id": "POZO-006", "base_production": 180.0, "active_from": date(2018, 11, 20)},
    {"id": "POZO-007", "base_production": 420.0, "active_from": date(2016, 5, 10)},
    {"id": "POZO-008", "base_production": 120.0, "active_from": date(2020, 9, 5)},
    {"id": "POZO-009", "base_production": 95.0, "active_from": date(2024, 2, 14)},
    {"id": "POZO-010", "base_production": 275.0, "active_from": date(2017, 8, 30)},
    {"id": "POZO-011", "base_production": 60.0, "active_from": date(2023, 11, 1)},
    {"id": "POZO-012", "base_production": 310.0, "active_from": date(2019, 4, 22)},
    {"id": "POZO-013", "base_production": 165.0, "active_from": date(2022, 10, 12)},
    {"id": "POZO-014", "base_production": 240.0, "active_from": date(2018, 2, 3)},
    {"id": "POZO-015", "base_production": 135.0, "active_from": date(2021, 12, 8)},
    {"id": "POZO-016", "base_production": 190.0, "active_from": date(2020, 6, 18)},
    {"id": "POZO-017", "base_production": 50.0, "active_from": date(2024, 8, 25)},
    {"id": "POZO-018", "base_production": 380.0, "active_from": date(2016, 1, 7)},
    {"id": "POZO-019", "base_production": 110.0, "active_from": date(2023, 3, 17)},
    {"id": "POZO-020", "base_production": 220.0, "active_from": date(2019, 9, 29)},
]


def get_well(id_well: str) -> dict:
    """Return the well record for id_well.

    Wells in WELLS get their configured base_production. Any other ID (e.g. real
    idpozo from the DW returned by /wells) gets the default base_production so
    /forecast accepts it and returns a valid synthetic forecast.
    """
    record = next((w for w in WELLS if w["id"] == id_well), None)
    if record is not None:
        return record
    return {"id": id_well, "base_production": _DEFAULT_BASE_PRODUCTION, "active_from": date(2006, 1, 1)}


def well_exists(id_well: str) -> bool:
    """Any non-empty ID is valid: /wells returns real DW IDs that /forecast must accept."""
    return bool(id_well and id_well.strip())
