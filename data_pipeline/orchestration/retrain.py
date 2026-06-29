"""Orquestación del reentrenamiento del modelo (Fase 3, Rol 2, ADR-041).

Job `retrain` particionado por día ("reentrenar como si fuera el día X") que
encadena el flujo que pide la adenda:

    refrescar features (materializa el feature store) → entrenar (ml/) → registrar en MLflow

Disparo (adenda 2.4): además de correrlo a mano,
- **Schedule mensual** alineado al refresh del DW (ADR-021, cron día 5): el retrain
  corre el día 6, cuando ya hay features nuevas del mes.
- **Sensor por llegada de datos**: dispara cuando el feature store
  (`features.feat_produccion_pozo_mensual`, ADR-036) tiene un período nuevo.

Ambos requieren el **dagster-daemon** corriendo (ver runbook ml-retrain). Todo es
env-driven (`POSTGRES_*`, `MLFLOW_TRACKING_URI`): el mismo código sirve a staging y prod.
"""

import os
from datetime import date

from dagster import (
    AssetExecutionContext,
    AssetSelection,
    Backoff,
    DailyPartitionsDefinition,
    MaterializeResult,
    RetryPolicy,
    RunRequest,
    SkipReason,
    asset,
    define_asset_job,
    schedule,
    sensor,
)

from data_pipeline.config import PROJECT_ROOT

# Partición por día para reprocesar "como si fuera el día X" (adenda 2.3/2.5).
# end_offset=1 hace válida la partición del día en curso (para que Schedule/Sensor
# puedan disparar hoy). Ver runbook ml-retrain para el backfill.
_RETRAIN_PARTITIONS = DailyPartitionsDefinition(start_date="2024-01-01", end_offset=1)
_RETRY = RetryPolicy(max_retries=2, delay=10, backoff=Backoff.EXPONENTIAL)


def _train_cmd() -> list[str]:
    """Comando del paso de entrenamiento, configurable por env.

    Por defecto orquesta `ml.baseline`, que entrena/evalúa y **loguea el run en
    MLflow** (cadena completa demostrable). `train.py` (campeón) aún **no** loguea a
    MLflow a propósito (el tracking/registro es de Rol 3, ver el handoff de MLflow);
    cuando Rol 3 enchufe ese logging (p. ej. `ml.train --mlflow`), basta exportar
    `RETRAIN_CMD="python -m ml.train --mlflow"`. No se hardcodea para no pisar su zona.
    """
    import sys

    return os.getenv("RETRAIN_CMD", f"{sys.executable} -m ml.baseline").split()


@asset(partitions_def=_RETRAIN_PARTITIONS, group_name="retrain", retry_policy=_RETRY)
def features_refrescadas(context: AssetExecutionContext) -> MaterializeResult:
    """Refresca el feature store (lo materializa) antes de entrenar.

    Reusa la materialización del store (ADR-036): corre el pipeline de features de
    `ml/` sobre el crudo de Bronze y reescribe `features.feat_produccion_pozo_mensual`.
    Asume que Bronze ya está fresco (lo deja el refresh mensual del DW, ADR-018).
    """
    from data_pipeline.orchestration import feature_store_build as fsb

    filas = fsb.materializar(fsb.engine_from_env())
    return MaterializeResult(metadata={"filas": filas, "asof": context.partition_key})


@asset(
    partitions_def=_RETRAIN_PARTITIONS,
    deps=[features_refrescadas],
    group_name="retrain",
    retry_policy=_RETRY,
)
def modelo_reentrenado(context: AssetExecutionContext) -> MaterializeResult:
    """Entrena y registra el run en MLflow para la fecha de la partición.

    Corre `RETRAIN_CMD` (default `ml.baseline`, que loguea a MLflow). La fecha de
    corte ("como si fuera el día X") se pasa por `RETRAIN_ASOF`; el entrenamiento
    debe respetarla para no usar datos posteriores (anti-leakage). El tracking apunta
    a `MLFLOW_TRACKING_URI` (servidor MLflow de Rol 3, ADR-037) si está seteado.
    """
    import subprocess

    asof = context.partition_key
    cmd = _train_cmd()
    context.log.info(f"retrain asof={asof} cmd={' '.join(cmd)}")
    subprocess.run(
        cmd,
        check=True,
        cwd=str(PROJECT_ROOT),
        env={**os.environ, "RETRAIN_ASOF": asof},
    )
    return MaterializeResult(metadata={"asof": asof, "cmd": " ".join(cmd)})


retrain_job = define_asset_job(
    name="retrain",
    selection=AssetSelection.assets("features_refrescadas", "modelo_reentrenado"),
)


@schedule(job=retrain_job, cron_schedule="0 6 6 * *")
def retrain_mensual(context):
    """Retrain mensual: día 6 a las 06:00, después del refresh del DW (cron día 5)."""
    fecha = context.scheduled_execution_time.strftime("%Y-%m-%d")
    return RunRequest(partition_key=fecha, run_key=f"sched-{fecha}")


def _ultimo_periodo_features() -> str | None:
    """Máximo `periodo` del feature store, o None si está vacío/inaccesible."""
    from sqlalchemy import create_engine, text

    url = (
        f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'oil')}:"
        f"{os.getenv('POSTGRES_PASSWORD', 'oil')}@{os.getenv('POSTGRES_HOST', 'localhost')}:"
        f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'oil_dw')}"
    )
    try:
        with create_engine(url).connect() as conn:
            valor = conn.execute(
                text("select max(periodo) from features.feat_produccion_pozo_mensual")
            ).scalar()
        return str(valor) if valor is not None else None
    except Exception:  # noqa: BLE001 — una caída de DB no debe tumbar el daemon
        return None


@sensor(job=retrain_job, minimum_interval_seconds=3600)
def retrain_por_features_nuevas(context):
    """Dispara el retrain cuando el feature store tiene un período nuevo (adenda 2.4)."""
    ultimo = _ultimo_periodo_features()
    if ultimo is None:
        return SkipReason("feature store vacío o inaccesible")
    if context.cursor == ultimo:
        return SkipReason("sin features nuevas desde el último disparo")
    context.update_cursor(ultimo)
    hoy = date.today().strftime("%Y-%m-%d")
    return RunRequest(run_key=f"datos-{ultimo}", partition_key=hoy)
