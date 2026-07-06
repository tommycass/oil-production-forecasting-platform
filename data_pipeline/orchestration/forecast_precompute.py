"""Precómputo del pronóstico mensual por pozo (Fase 3, Rol 2, ADR-043).

Corre el **mismo motor recursivo** que sirve `/forecast` (`ml.forecast`, ADR-042) sobre
**todos los pozos** del feature store, con el modelo **Production** del registry MLflow,
y persiste el resultado en `features.pred_produccion_pozo_mensual` (petróleo) y
`..._gas` (gas): 12 meses hacia adelante por pozo, contados desde su último mes
observado. La API sirve estas filas como **lookup** cuando están frescas (mismo
`ultimo_observado` que el store) y recae al motor on-the-fly si no — transparente
para el usuario: mismo modelo + mismas features ⇒ **mismos valores**.

Se materializa **dentro del job de retrain** (asset `forecast_precomputado`, ADR-040),
después de refrescar el store y reentrenar/promover: así el precómputo siempre refleja
el último modelo Production sobre las últimas features. Si un target no tiene modelo en
Production todavía, se lo saltea (la API sigue funcionando por el camino on-the-fly).

No importa dagster: la lógica es testeable aislada (`build_predicciones` es pura).
"""

from __future__ import annotations

import os

import pandas as pd
from sqlalchemy import text

from data_pipeline.orchestration.feature_store_build import (
    FEATURE_SCHEMA,
    engine_from_env,  # noqa: F401 — re-export: el asset arma el engine desde acá
    table_for,
)
from ml.config import TARGETS, experiment_name

# Horizonte del precómputo, en meses = MAX_FORECAST_MONTHS de la API (ADR-042/043).
# Deben coincidir: si la API pidiera más meses de los precomputados caería al motor
# on-the-fly (guarda de completitud del servicio), correcto pero sin lookup.
N_STEPS = 12

PRED_TABLE = "pred_produccion_pozo_mensual"  # petróleo (misma convención que el store)

# Claves/target de la tabla del store: todo lo demás es feature del modelo (ADR-035).
_NON_FEATURE = {"idpozo", "periodo", "periodo_objetivo", "y_next"}


def pred_table_for(target: str) -> str:
    """Tabla de predicciones para un ``target`` (petróleo sin sufijo; gas ``_gas``,
    misma convención que `table_for` del store, ADR-039)."""
    return PRED_TABLE if target == "prod_pet" else f"{PRED_TABLE}_{target.removeprefix('prod_')}"


def _model_name(target: str) -> str:
    """Nombre del modelo en el registry, con el MISMO override por env que usa la API
    (`model_loader.MODEL_NAME_BY_TARGET`): así precómputo y serving cargan el mismo."""
    env = "MLFLOW_MODEL_NAME" if target == "prod_pet" else "MLFLOW_MODEL_NAME_GAS"
    return os.getenv(env, experiment_name(target))


def cargar_modelo_production(target: str):
    """Carga el modelo **Production** del target desde el registry MLflow.

    Returns:
        ``(pipeline, nombre, version)``, o ``None`` si no hay modelo en Production
        (registry vacío o inalcanzable). ``None`` NO es un error del job: significa
        "sin precómputo para este target" y la API sigue sirviendo on-the-fly.
    """
    import mlflow
    import mlflow.sklearn
    from mlflow.tracking import MlflowClient

    from ml.config import MLFLOW_TRACKING_URI

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    nombre = _model_name(target)
    try:
        versiones = MlflowClient().get_latest_versions(nombre, stages=["Production"])
        if not versiones:
            return None
        pipeline = mlflow.sklearn.load_model(f"models:/{nombre}/Production")
        return pipeline, nombre, versiones[0].version
    except Exception:  # registered model inexistente / MLflow caído → sin precómputo
        return None


def build_predicciones(
    store: pd.DataFrame, pipeline, target: str, n_steps: int = N_STEPS
) -> pd.DataFrame:
    """Pronóstico recursivo de ``n_steps`` meses para **cada pozo** de la tabla del store.

    Función **pura** (recibe el store en memoria y el pipeline; no toca DB/MLflow).
    Por pozo replica exactamente lo que hace el serving on-the-fly (`ml.forecast` +
    `feature_reader`): fila del último mes = features base, serie observada del target
    para el recompute, estáticas replicadas. Un pozo cuyo forecast falla se saltea
    (no voltea el batch completo).

    Returns:
        DataFrame ``[idpozo, periodo, prediccion, ultimo_observado]`` con ``n_steps``
        filas por pozo (``periodo`` = mes pronosticado, desde ``ultimo_observado + 1``).
    """
    from ml.forecast import recursive_forecast

    store = store.sort_values(["idpozo", "periodo"])
    # Anclas estáticas = las columnas del store que no son autorregresivas del target
    # ni claves (mismo criterio que feature_reader.STATIC_FEATURE_COLUMNS de la API).
    estaticas = [
        c for c in store.columns
        if c not in _NON_FEATURE and not c.startswith(target) and c != "well_age_months"
    ]

    filas: list[dict] = []
    for idpozo, panel in store.groupby("idpozo", sort=False):
        base_row = panel.iloc[-1]
        base_features = {k: v for k, v in base_row.items() if k not in _NON_FEATURE}
        static = {c: base_row[c] for c in estaticas}
        static["idpozo"] = idpozo
        series = panel[["periodo", target]]
        ultimo = pd.Timestamp(panel["periodo"].max())
        try:
            preds = recursive_forecast(
                pipeline, base_features, series, static, target, n_steps
            )
        except Exception:  # un pozo dañado no voltea el precómputo de los demás
            continue
        filas.extend(
            {
                "idpozo": idpozo,
                "periodo": p["periodo"],
                "prediccion": p[target],
                "ultimo_observado": ultimo,
            }
            for p in preds
        )
    return pd.DataFrame(filas, columns=["idpozo", "periodo", "prediccion", "ultimo_observado"])


def precomputar(engine, target: str, n_steps: int = N_STEPS) -> int | None:
    """Precomputa y escribe la tabla de predicciones de un ``target``.

    Returns:
        Filas escritas, o ``None`` si el target no tiene modelo en Production
        (se saltea sin escribir: la tabla anterior, si existía, queda como estaba
        y la API decide por frescura).
    """
    cargado = cargar_modelo_production(target)
    if cargado is None:
        return None
    pipeline, nombre, version = cargado

    store = pd.read_sql(
        f"select * from {FEATURE_SCHEMA}.{table_for(target)}", engine
    )
    df = build_predicciones(store, pipeline, target, n_steps)
    df["model_name"] = nombre
    df["model_version"] = str(version)
    df["generado_en"] = pd.Timestamp.now(tz="UTC")

    with engine.begin() as conn:
        conn.execute(text(f"create schema if not exists {FEATURE_SCHEMA}"))
    df.to_sql(pred_table_for(target), engine, schema=FEATURE_SCHEMA, if_exists="replace", index=False)
    return len(df)


def precomputar_todos(engine, targets=TARGETS, n_steps: int = N_STEPS) -> dict[str, int | None]:
    """Precomputa las predicciones de **todos los targets** (petróleo + gas, ADR-039).

    Es lo que invoca el asset `forecast_precomputado` del job de retrain (ADR-040/043).
    Devuelve ``{target: filas | None}`` (``None`` = sin modelo Production, salteado).
    """
    return {target: precomputar(engine, target, n_steps) for target in targets}
