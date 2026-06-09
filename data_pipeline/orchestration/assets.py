"""Assets de Dagster: ingesta a Bronze (parquet) + carga al DW + modelos dbt.

El grafo cubre el flujo Medallion completo end-to-end (ADR-011, ADR-014):

  Data Sources → Bronze(parquet) → Bronze(Postgres) → Silver → Gold + Data Quality

- Bronze parquet (Data Engineer): assets que envuelven `data_pipeline/extraction`
  (la lógica vive ahí; Dagster aporta ejecución, logs, status, reintentos y
  particiones, no reimplementa la extracción).
- Bronze→Postgres (Analytics Engineer): corren `transform/scripts/load_bronze.py`.
- Silver/Gold/DQ (Analytics Engineer): modelos dbt vía dagster-dbt; los tests de
  calidad aparecen como asset checks y un check `error` frena la promoción a Gold.

Producción se modela en dos assets para habilitar el backfill por mes sin
re-descargar:
  - `produccion_raw`: baja el CSV completo a landing (una vez).
  - `bronze_produccion`: particionado por mes; cada partición lee del landing y
    escribe solo su mes. El refresh automático (run_pipeline.sh) materializa todas
    las particiones (full reload, ADR-020); reprocesar un mes puntual = materializar
    esa partición.
"""

import subprocess
import sys

from dagster import (
    AssetExecutionContext,
    AssetKey,
    Backoff,
    MaterializeResult,
    MonthlyPartitionsDefinition,
    RetryPolicy,
    asset,
)
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, dbt_assets

from data_pipeline.config import PROJECT_ROOT
from data_pipeline.extraction.extract_pozos import extract_pozos
from data_pipeline.extraction.extract_produccion import descargar_landing, escribir_particion
from data_pipeline.orchestration.dbt_project import dw_dbt_project

# Reintentos con backoff exponencial: la extracción descarga por HTTP desde
# datos.gob.ar, que puede fallar de forma transitoria. Reintenta a los 5s, 10s
# y 20s. La extracción es idempotente, así que reintentar es seguro.
_RETRY_POLICY = RetryPolicy(max_retries=3, delay=5, backoff=Backoff.EXPONENTIAL)

# Particiones mensuales de producción. La fuente arranca en enero de 2006.
_PARTICIONES_MENSUALES = MonthlyPartitionsDefinition(start_date="2006-01-01")


# --- Bronze parquet (Data Engineer) ---------------------------------------


@asset(group_name="bronze", retry_policy=_RETRY_POLICY)
def bronze_pozos(context: AssetExecutionContext) -> MaterializeResult:
    """Listado de pozos crudo en Bronze (full refresh)."""
    archivo = extract_pozos()
    context.log.info(f"pozos → {archivo}")
    return MaterializeResult(metadata={"ruta": str(archivo)})


@asset(group_name="landing", retry_policy=_RETRY_POLICY)
def produccion_raw(context: AssetExecutionContext) -> MaterializeResult:
    """Descarga el CSV completo de producción a landing (una sola descarga)."""
    archivo = descargar_landing()
    context.log.info(f"landing → {archivo}")
    return MaterializeResult(metadata={"ruta": str(archivo)})


@asset(
    group_name="bronze",
    partitions_def=_PARTICIONES_MENSUALES,
    deps=[produccion_raw],
    retry_policy=_RETRY_POLICY,
)
def bronze_produccion(context: AssetExecutionContext) -> MaterializeResult:
    """Partición Bronze de producción del mes, derivada del landing.

    La `partition_key` llega como 'AAAA-MM-01'; se traduce a anio/mes (sin cero a
    la izquierda, como vienen en la fuente) y se escribe solo esa partición.
    """
    anio, mes_pad, _ = context.partition_key.split("-")
    mes = str(int(mes_pad))
    archivo = escribir_particion(anio, mes)
    context.log.info(f"produccion {anio}-{mes} → {archivo}")
    return MaterializeResult(metadata={"ruta": str(archivo), "anio": anio, "mes": mes})


# --- Bronze parquet → Postgres (Analytics Engineer) -----------------------
# Reusa transform/scripts/load_bronze.py (sigue siendo el puente parquet→PG; no
# se reimplementa). Se invoca por subprocess con el intérprete del propio venv
# (tiene pandas/sqlalchemy/psycopg2) y hereda POSTGRES_* del entorno, así el
# mismo asset sirve a staging (oil_dw_staging) y prod (oil_dw_prod).
_LOAD_BRONZE = PROJECT_ROOT / "transform" / "scripts" / "load_bronze.py"


def _cargar_a_postgres(context: AssetExecutionContext, fuente: str) -> MaterializeResult:
    context.log.info(f"load_bronze → bronze.{fuente}")
    subprocess.run(
        [sys.executable, str(_LOAD_BRONZE), "--fuente", fuente],
        check=True,
        cwd=str(PROJECT_ROOT),
    )
    return MaterializeResult(metadata={"fuente": fuente, "schema": "bronze"})


@asset(
    key=AssetKey(["bronze", "produccion"]),
    deps=[bronze_produccion],
    group_name="bronze_db",
)
def bronze_produccion_db(context: AssetExecutionContext) -> MaterializeResult:
    """Carga las particiones Bronze de producción a `bronze.produccion` (Postgres)."""
    return _cargar_a_postgres(context, "produccion")


@asset(
    key=AssetKey(["bronze", "pozos"]),
    deps=[bronze_pozos],
    group_name="bronze_db",
)
def bronze_pozos_db(context: AssetExecutionContext) -> MaterializeResult:
    """Carga el snapshot Bronze de pozos a `bronze.pozos` (Postgres)."""
    return _cargar_a_postgres(context, "pozos")


# --- Silver / Gold / Data Quality (Analytics Engineer, vía dagster-dbt) ----


class _DwDbtTranslator(DagsterDbtTranslator):
    """Liga las dbt sources `bronze.*` a las asset keys de los assets de carga.

    Así los modelos Silver que hacen `source('bronze','produccion'|'pozos')` quedan
    downstream de `bronze_produccion_db` / `bronze_pozos_db` y el grafo se conecta solo.
    """

    def get_asset_key(self, dbt_resource_props):
        if dbt_resource_props["resource_type"] == "source":
            return AssetKey([dbt_resource_props["source_name"], dbt_resource_props["name"]])
        return super().get_asset_key(dbt_resource_props)


# @dbt_assets lee el manifest al decorar: solo se define si existe (lo deja
# `dbt parse` en el setup). Sin manifest el grafo carga igual con Bronze + carga,
# y los modelos dbt aparecen una vez generado el manifest.
if dw_dbt_project.manifest_path.exists():

    @dbt_assets(
        manifest=dw_dbt_project.manifest_path,
        dagster_dbt_translator=_DwDbtTranslator(),
    )
    def dw_dbt_models(context: AssetExecutionContext, dbt: DbtCliResource):
        """Materializa Silver/Gold y corre Data Quality (`dbt build`)."""
        yield from dbt.cli(["build"], context=context).stream()
