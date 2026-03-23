from datetime import date


def get_wells(date_query: date) -> list[dict]:
    """Retorna la lista mock de pozos activos a la fecha indicada."""
    return [
        {"id_well": "POZO-001"},
        {"id_well": "POZO-002"},
        {"id_well": "POZO-003"},
    ]
