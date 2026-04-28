from datetime import date
from app.core.demo_data import WELLS


def get_wells(date_query: date) -> list[dict]:
    """Return the wells that were active on the given date."""
    return [{"id_well": w["id"]} for w in WELLS if w["active_from"] <= date_query]
