"""Assets de Dagster de la ingesta a Bronze.

Cada asset llama a las funciones de extracción ya existentes (la lógica vive en
data_pipeline/extraction). Dagster aporta ejecución, logs, status, reintentos y
particiones; no reimplementa la extracción. Ver ADR-011.

Producción se modela en dos assets para habilitar el backfill por mes sin
re-descargar:
  - `produccion_raw`: baja el CSV completo a landing (una vez).
  - `bronze_produccion`: particionado por mes; cada partición lee del landing y
    escribe solo su mes. Reprocesar un mes = materializar esa partición.
"""

from dagster import (
    AssetExecutionContext,
    Backoff,
    MaterializeResult,
    MonthlyPartitionsDefinition,
    RetryPolicy,
    asset,
)

from data_pipeline.extraction.extract_pozos import extract_pozos
from data_pipeline.extraction.extract_produccion import descargar_landing, escribir_particion

# Reintentos con backoff exponencial: la extracción descarga por HTTP desde
# datos.gob.ar, que puede fallar de forma transitoria. Reintenta a los 5s, 10s
# y 20s. La extracción es idempotente, así que reintentar es seguro.
_RETRY_POLICY = RetryPolicy(max_retries=3, delay=5, backoff=Backoff.EXPONENTIAL)

# Particiones mensuales de producción. La fuente arranca en enero de 2006.
_PARTICIONES_MENSUALES = MonthlyPartitionsDefinition(start_date="2006-01-01")


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
