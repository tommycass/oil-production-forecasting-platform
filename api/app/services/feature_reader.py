"""Lee features del feature store para inferencia en tiempo real.

El feature store (ADR-036) materializa **una tabla por target** (ADR-042):
petróleo y gas. Cada fila es el **mes base `t`** (clave `idpozo` + `periodo`) con las
29 features con las que el modelo predice el target del **mes siguiente `t+1`**.

Para inferir `t+1 = (año, mes)`, la API lee la fila del mes base
`t = (año, mes) − 1 mes` en la tabla del target pedido y pasa **todas** las features
al Pipeline del modelo (que hace one-hot/imputación internamente). No recalcula nada
(evita el training-serving skew, RNF de la Fase 3).

Contrato completo de columnas: docs/feature-store.md.
"""

from __future__ import annotations

from datetime import date

from app.core.database import fetch_all

# Tabla del store por target (ADR-036 Rev. 2 / ADR-042). Petróleo mantiene el nombre
# histórico; gas usa el sufijo _gas. La API elige la tabla según el target pedido.
FEATURE_TABLE_BY_TARGET = {
    "prod_pet": "features.feat_produccion_pozo_mensual",
    "prod_gas": "features.feat_produccion_pozo_mensual_gas",
}

# Columnas que NO son features del modelo: claves de lookup y el target de training.
# Se descartan de la fila; el resto (las 29 features) va al Pipeline. Derivar las
# features "por descarte" evita hardcodear los nombres engineered, que cambian por
# target (prod_pet_* vs prod_gas_*), y mantiene una sola fuente de verdad (el store).
NON_FEATURE_COLUMNS = {"idpozo", "periodo", "periodo_objetivo", "y_next"}


def _mes_base(anio: int, mes: int) -> date:
    """Primer día del mes base `t` = primer día del mes objetivo `(anio, mes)` menos
    un mes. Es la clave `periodo` con la que se busca la fila en el store."""
    if mes == 1:
        return date(anio - 1, 12, 1)
    return date(anio, mes - 1, 1)


def get_features_for_inference(idpozo: int, anio: int, mes: int, target: str) -> dict:
    """Retorna las 29 features del mes base `t` para predecir el target del mes
    `t+1 = (anio, mes)` en el pozo dado.

    Args:
        idpozo: ID numérico del pozo.
        anio: Año del mes a PREDECIR (t+1).
        mes: Mes a predecir (1-12, t+1).
        target: ``prod_pet`` (petróleo) o ``prod_gas`` (gas) — elige la tabla del store.

    Returns:
        Dict ``{columna_feature: valor}`` con las 29 features del mes base `t`
        (sin ``idpozo``/``periodo``/``periodo_objetivo``/``y_next``).

    Raises:
        ValueError: Si el ``target`` no está soportado, o si el pozo no tiene fila en
            el mes base (pozo nuevo o sin historia a esa fecha).
    """
    tabla = FEATURE_TABLE_BY_TARGET.get(target)
    if tabla is None:
        raise ValueError(
            f"Target '{target}' no soportado. Opciones: {sorted(FEATURE_TABLE_BY_TARGET)}"
        )

    periodo_t = _mes_base(anio, mes)

    # SELECT * y se descartan las no-feature: así el reader no depende de los nombres
    # engineered (distintos por target) y toma automáticamente cualquier feature nueva
    # que el Rol 2 re-materialice (el nombre de tabla es de una allowlist, no del input).
    sql = f"""
        SELECT *
        FROM {tabla}
        WHERE idpozo = :idpozo
          AND periodo = :periodo
        LIMIT 1
    """
    rows = fetch_all(sql, {"idpozo": idpozo, "periodo": periodo_t})
    if not rows:
        raise ValueError(
            f"Pozo {idpozo} sin features en el mes base {periodo_t.isoformat()} "
            f"(para predecir {target} de {anio}-{mes:02d})"
        )

    return {k: v for k, v in rows[0].items() if k not in NON_FEATURE_COLUMNS}
