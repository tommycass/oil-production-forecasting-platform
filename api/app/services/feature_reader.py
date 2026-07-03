"""Lectura del feature store para el **forecast recursivo** (ADR-044).

El forecast recursivo necesita, para sembrar la recursión, la **serie mensual observada**
del pozo (no solo la fila del mes base): a partir de ella `ml.forecast` recalcula en cada
paso las features recursion-safe (ADR-043) y encadena las predicciones.

Este módulo expone el **contrato** que consume el servicio de forecast. La lectura concreta
del store/DW (SQL + materialización de las columnas recursion-safe) es la **adaptación del
feature store**, a cargo del Rol 2 — ver ``get_history_for_forecast``.

Contrato de columnas del store: docs/feature-store.md.
"""

from __future__ import annotations

# Tabla del store por target (ADR-036 Rev. 2 / ADR-042). Petróleo mantiene el nombre
# histórico; gas usa el sufijo _gas. El reader elige la tabla según el target.
FEATURE_TABLE_BY_TARGET = {
    "prod_pet": "features.feat_produccion_pozo_mensual",
    "prod_gas": "features.feat_produccion_pozo_mensual_gas",
}

# Atributos ESTÁTICOS del pozo que el modelo recursion-safe usa como features (ADR-043):
# no cambian mes a mes, así que se leen una vez y se replican en cada paso de la recursión.
STATIC_FEATURE_COLUMNS = [
    "profundidad", "coordenadax", "coordenaday",
    "tipoextraccion", "tipoestado", "tipopozo", "empresa", "formprod", "formacion",
    "areapermisoconcesion", "areayacimiento", "cuenca", "provincia", "proyecto",
    "clasificacion", "subclasificacion", "sub_tipo_recurso",
]


def get_history_for_forecast(idpozo: int, target: str):
    """Datos del feature store para el forecast recursivo de un pozo (ADR-044).

    Args:
        idpozo: ID numérico del pozo.
        target: ``prod_pet`` (petróleo) o ``prod_gas`` (gas) — elige la tabla del store.

    Returns:
        Tupla ``(base_features, series, static)``:
          - ``base_features``: ``dict`` ``{columna: valor}`` con las **features del mes base**
            ``t`` (último mes observado) leídas **directamente del store**. El forecast las usa
            para predecir el **primer** mes (t+1) **sin recalcular** — así el store se usa en
            inferencia (RNF Fase 3). Es la misma fila que consumía ``/predict``.
          - ``series``: ``pandas.DataFrame`` con **una fila por mes observado**, columnas
            ``periodo`` (``date``, 1° de mes) y ``target`` (valor del target), **ordenada
            ascendente**. Se usa para **recalcular** las features de los meses futuros (t+2+),
            que no existen en el store.
          - ``static``: ``dict`` con los atributos de ``STATIC_FEATURE_COLUMNS`` (+ ``idpozo``),
            constantes en el tiempo; se replican en los meses recalculados.

    Raises:
        ValueError: si el ``target`` no está soportado, o si el pozo no tiene serie en el
            store (fuera del universo / sin historia) → el servicio lo traduce a 404.

    Nota (Rol 2 — adaptación del feature store): la implementación real, sobre
    ``FEATURE_TABLE_BY_TARGET[target]``: (1) lee la fila del **mes base** ``t`` (el último
    ``periodo`` del pozo) con **todas** las columnas de features → ``base_features``; (2) lee
    la serie ``(periodo, <target>)`` de todos los meses del pozo → ``series``; (3) toma
    ``STATIC_FEATURE_COLUMNS`` de esa fila → ``static``. Requiere que el store materialice las
    columnas recursion-safe con paridad training-serving (ADR-036/043). Hasta que esté
    implementada, ``/forecast`` responde **503** (no operativo).
    """
    if target not in FEATURE_TABLE_BY_TARGET:
        raise ValueError(
            f"Target '{target}' no soportado. Opciones: {sorted(FEATURE_TABLE_BY_TARGET)}"
        )
    raise NotImplementedError(
        "get_history_for_forecast: lectura del feature store pendiente "
        "(adaptación del feature store — Rol 2, ADR-044). Contrato en el docstring."
    )
