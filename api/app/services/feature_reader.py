"""Lectura del feature store y del precómputo para ``/forecast`` (ADR-044/045).

El forecast recursivo necesita, para arrancar, la fila de features del **mes base** (último
mes observado del pozo) y la **serie mensual observada** del target. Con la fila base se
predice el primer mes (t+1) **usando las features pre-computadas del store** (sin recalcular,
RNF de Fase 3); con la serie se recalculan las features de los meses futuros (t+2+), que no
existen en el store. Todo se lee de la **misma tabla** que materializa el training
(``feature_store_build.py``, ADR-036) → paridad training-serving garantizada.

Además (ADR-045) el retrain deja el pronóstico de 12 meses **precomputado** en
``features.pred_produccion_pozo_mensual`` (y ``_gas``): ``get_precomputed_forecast``
lo lee y el servicio lo sirve como lookup cuando está **fresco** (mismo último mes
observado que el store); si no, cae al motor recursivo on-the-fly. Transparente
para el usuario: mismo contrato, mismos valores (mismo modelo y mismas features).

Contrato de columnas del store y del precómputo: docs/feature-store.md.
"""

from __future__ import annotations

from app.core.database import fetch_all

# Tabla del store por target (ADR-036 Rev. 2 / ADR-042). Petróleo mantiene el nombre
# histórico; gas usa el sufijo _gas. El reader elige la tabla según el target.
FEATURE_TABLE_BY_TARGET = {
    "prod_pet": "features.feat_produccion_pozo_mensual",
    "prod_gas": "features.feat_produccion_pozo_mensual_gas",
}

# Tabla de pronósticos precomputados por target (ADR-045), misma convención de sufijo.
PRED_TABLE_BY_TARGET = {
    "prod_pet": "features.pred_produccion_pozo_mensual",
    "prod_gas": "features.pred_produccion_pozo_mensual_gas",
}

# Columnas que NO son features del modelo: claves de lookup y el target de training. Se
# descartan de la fila del store. Excluir ``y_next`` es además una **guarda anti-leak**: es
# el target del mes siguiente (justo lo que se predice) y no debe entrar como feature.
NON_FEATURE_COLUMNS = {"idpozo", "periodo", "periodo_objetivo", "y_next"}

# Anclas ESTÁTICAS del pozo que el set de ganancia positiva usa como features (ADR-043):
# no cambian mes a mes, así que se leen una vez y se replican en cada paso de la
# recursión. (`well_age_months` no va acá: se recalcula por paso; `mes` lo pone el motor
# con el mes objetivo.) Es la UNIÓN de las categóricas estáticas de ambos targets: el
# lector filtra por `if c in base_row`, así cada tabla (petróleo / gas) toma solo las que
# existen en su store.
STATIC_FEATURE_COLUMNS = [
    "areayacimiento", "profundidad", "coordenadax", "coordenaday",
    # petróleo
    "areapermisoconcesion", "tipopozo", "empresa", "proyecto", "cuenca",
    # gas
    "tipoestado", "clasificacion", "sub_tipo_recurso", "provincia", "formprod",
    # compartida
    "tipoextraccion",
]


def get_history_for_forecast(idpozo: int, target: str):
    """Datos del feature store para el forecast recursivo de un pozo (ADR-044).

    Args:
        idpozo: ID numérico del pozo.
        target: ``prod_pet`` (petróleo) o ``prod_gas`` (gas) — elige la tabla del store.

    Returns:
        Tupla ``(base_features, series, static)``:
          - ``base_features``: ``dict`` ``{columna: valor}`` con las **features del mes base**
            ``t`` (último mes observado), leídas **directamente del store** (sin ``y_next`` ni
            claves). Se usan para predecir el **primer** mes (t+1) **sin recalcular** — el store
            se usa en inferencia (RNF Fase 3).
          - ``series``: ``pandas.DataFrame`` con **una fila por mes observado**, columnas
            ``periodo`` y ``target``, ordenada ascendente. Se usa para **recalcular** las
            features de los meses futuros (t+2+), que no existen en el store.
          - ``static``: ``dict`` con los atributos de ``STATIC_FEATURE_COLUMNS`` (+ ``idpozo``).

    Raises:
        ValueError: si el ``target`` no está soportado, o si el pozo no tiene serie en el
            store (fuera del universo / sin historia) → el servicio lo traduce a 404.

    El store conserva la fila del **último mes** de cada pozo (con ``y_next`` NULL;
    ``feature_store_build`` usa left-join justo para habilitar la inferencia del mes
    siguiente), así que ``base_features`` del mes base **existe**. ``target`` y el nombre de
    tabla salen de una allowlist (no del input), así que interpolarlos en el SQL es seguro.
    """
    tabla = FEATURE_TABLE_BY_TARGET.get(target)
    if tabla is None:
        raise ValueError(
            f"Target '{target}' no soportado. Opciones: {sorted(FEATURE_TABLE_BY_TARGET)}"
        )

    # Fila del MES BASE (último mes observado): todas las columnas, se descartan las
    # no-feature (claves + y_next → guarda anti-leak). Es la fila que consumía /predict.
    base_rows = fetch_all(
        f"SELECT * FROM {tabla} WHERE idpozo = :idpozo ORDER BY periodo DESC LIMIT 1",
        {"idpozo": idpozo},
    )
    if not base_rows:
        raise ValueError(
            f"Pozo {idpozo} sin serie en el feature store del target '{target}' "
            f"(fuera del universo o sin historia)"
        )
    base_row = base_rows[0]
    base_features = {k: v for k, v in base_row.items() if k not in NON_FEATURE_COLUMNS}
    static = {c: base_row[c] for c in STATIC_FEATURE_COLUMNS if c in base_row}
    static["idpozo"] = idpozo

    # Serie observada (periodo + valor del target por mes) para recalcular los meses futuros.
    serie_rows = fetch_all(
        f"SELECT periodo, {target} FROM {tabla} WHERE idpozo = :idpozo ORDER BY periodo",
        {"idpozo": idpozo},
    )
    import pandas as pd

    series = pd.DataFrame(serie_rows, columns=["periodo", target])
    return base_features, series, static


def get_ultimo_periodo_observado(idpozo: int, target: str):
    """Último mes con dato real del pozo en el feature store (``max(periodo)``), o
    ``None`` si el pozo no tiene serie. Es el chequeo de **frescura** del precómputo
    (ADR-045): las predicciones precomputadas solo se sirven si arrancan exactamente
    después de este mes; si el store avanzó, el servicio recae al motor on-the-fly."""
    tabla = FEATURE_TABLE_BY_TARGET.get(target)
    if tabla is None:
        raise ValueError(
            f"Target '{target}' no soportado. Opciones: {sorted(FEATURE_TABLE_BY_TARGET)}"
        )
    rows = fetch_all(
        f"SELECT max(periodo) AS ultimo FROM {tabla} WHERE idpozo = :idpozo",
        {"idpozo": idpozo},
    )
    return rows[0]["ultimo"] if rows else None


def get_precomputed_forecast(idpozo: int, target: str):
    """Pronóstico **precomputado** del pozo (ADR-045): las filas que dejó el job de
    retrain en ``features.pred_produccion_pozo_mensual`` (o ``_gas``), ordenadas por mes.

    Returns:
        Lista de dicts ``{periodo, prediccion, ultimo_observado, ...}`` (una por mes
        futuro, típicamente 12), o ``None`` si no hay precómputo para el pozo — por
        tabla inexistente (aún no corrió el retrain con precómputo) o pozo sin filas.
        ``None`` NO es un error: el servicio cae al motor recursivo on-the-fly.
    """
    tabla = PRED_TABLE_BY_TARGET.get(target)
    if tabla is None:
        raise ValueError(
            f"Target '{target}' no soportado. Opciones: {sorted(PRED_TABLE_BY_TARGET)}"
        )
    try:
        rows = fetch_all(
            f"SELECT * FROM {tabla} WHERE idpozo = :idpozo ORDER BY periodo",
            {"idpozo": idpozo},
        )
    except Exception:  # tabla inexistente / esquema sin crear → sin precómputo, no es error
        return None
    return rows or None
