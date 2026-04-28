from datetime import date, timedelta
from app.core.demo_data import DECLINE_RATE_BBL_PER_DAY, get_well


def get_forecast(id_well: str, date_start: date, date_end: date) -> list[dict]:
    """Generate a daily production forecast using a linear decline model."""
    base = get_well(id_well)["base_production"]
    result = []
    current = date_start
    day = 0
    while current <= date_end:
        result.append({"date": current, "prod": round(base - day * DECLINE_RATE_BBL_PER_DAY, 2)})
        current += timedelta(days=1)
        day += 1
    return result
