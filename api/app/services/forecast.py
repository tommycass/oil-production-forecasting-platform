from datetime import date, timedelta

WELL_BASE_PRODUCTION = {
    "POZO-001": 200.0,
    "POZO-002": 150.0,
    "POZO-003": 100.0,
}


def get_forecast(id_well: str, date_start: date, date_end: date) -> list[dict]:
    """Genera pronóstico diario con modelo de declinación lineal (−0.5 bbl/día)."""
    base = WELL_BASE_PRODUCTION[id_well]
    result = []
    current = date_start
    day = 0
    while current <= date_end:
        result.append({"date": current, "prod": round(base - day * 0.5, 2)})
        current += timedelta(days=1)
        day += 1
    return result
