from datetime import date

from app.core.database import fetch_all

# Pozos con producción en el mes (anio, mes) que contiene la fecha consultada.
# El grano de la fact es (pozo, mes); se filtra el miembro desconocido (idpozo = -1).
# El id_well del contrato es el `idpozo` de la fuente (clave estable), como string.
_WELLS_SQL = """
    SELECT DISTINCT dp.idpozo::text AS id_well
    FROM gold.fact_produccion_mensual f
    JOIN gold.dim_pozo dp ON dp.sk_pozo = f.sk_pozo
    JOIN gold.dim_fecha df ON df.sk_fecha = f.sk_fecha
    WHERE df.anio = :anio
      AND df.mes = :mes
      AND dp.idpozo != -1
    ORDER BY id_well
"""


def get_wells(date_query: date) -> list[dict]:
    """Pozos con producción en el mes que contiene `date_query`, desde el DW (Gold).

    Devuelve una lista de dicts `{"id_well": "<idpozo>"}`, el formato que espera
    `WellResponse`.
    """
    return fetch_all(_WELLS_SQL, {"anio": date_query.year, "mes": date_query.month})
