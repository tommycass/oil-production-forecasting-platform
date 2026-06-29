"""Asset de Dagster que materializa el feature store (Fase 3, Rol 2).

Corre el pipeline de features de ML (reusa `ml/`, ver `feature_store_build.py`) y
escribe `features.feat_produccion_pozo_mensual` en el DW, leyendo el crudo de
`bronze.produccion`. Queda **downstream de la carga de Bronze**, así se refresca con
el pipeline del DW; el job de retrain también lo materializa antes de entrenar (ADR-041).

El import pesado (`feature_store_build` → pandas/sklearn/`ml`) es **lazy** dentro del
asset: así las Definitions cargan aunque el venv liviano no tenga esas deps (el venv
del daemon sí las necesita; ver runbook). No usar `from __future__ import annotations`
acá: rompe la validación de `context: AssetExecutionContext` de Dagster.
"""

from dagster import AssetExecutionContext, AssetKey, Backoff, MaterializeResult, RetryPolicy, asset

_RETRY = RetryPolicy(max_retries=2, delay=10, backoff=Backoff.EXPONENTIAL)


@asset(
    deps=[AssetKey(["bronze", "produccion"])],
    group_name="features",
    retry_policy=_RETRY,
)
def feature_store(context: AssetExecutionContext) -> MaterializeResult:
    """Materializa el feature store desde Bronze (las 29 features del modelo)."""
    from data_pipeline.orchestration import feature_store_build as fsb

    filas = fsb.materializar(fsb.engine_from_env())
    tabla = f"{fsb.FEATURE_SCHEMA}.{fsb.FEATURE_TABLE}"
    context.log.info(f"feature store → {tabla}: {filas} filas")
    return MaterializeResult(metadata={"tabla": tabla, "filas": filas})
