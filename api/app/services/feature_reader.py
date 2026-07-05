"""Lectura del feature store para el **forecast recursivo** (ADR-044).

El forecast recursivo necesita, para arrancar, la fila de features del **mes base** (último
mes observado del pozo) y la **serie mensual observada** del target. Con la fila base se
predice el primer mes (t+1) **usando las features pre-computadas del store** (sin recalcular,
RNF de Fase 3); con la serie se recalculan las features de los meses futuros (t+2+), que no
existen en el store. Todo se lee de la **misma tabla** que materializa el training
(``feature_store_build.py``, ADR-036) → paridad training-serving garantizada.

Contrato de columnas del store: docs/feature-store.md.
"""

from __future__ import annotations

from app.core.database import fetch_all

# Tabla del store por target (ADR-036 Rev. 2 / ADR-042). Petróleo mantiene el nombre
# histórico; gas usa el sufijo _gas. El reader elige la tabla según el target.
FEATURE_TABLE_BY_TARGET = {
    "prod_pet": "features.feat_produccion_pozo_mensual",
    "prod_gas": "features.feat_produccion_pozo_mensual_gas",
}

# Columnas que NO son features del modelo: claves de lookup y el target de training. Se
# descartan de la fila del store. Excluir ``y_next`` es además una **guarda anti-leak**: es
# el target del mes siguiente (justo lo que se predice) y no debe entrar como feature.
NON_FEATURE_COLUMNS = {"idpozo", "periodo", "periodo_objetivo", "y_next"}

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
