from datetime import date


def get_wells(date_query: date) -> list[dict]:
    """Return the mock list of active wells for the given date."""
    return [
        {"id_well": "POZO-001"},
        {"id_well": "POZO-002"},
        {"id_well": "POZO-003"},
    ]
