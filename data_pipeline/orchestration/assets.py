"""Assets de Dagster que envuelven la extracción a la capa Bronze.

Cada asset llama a la función de extracción ya existente (la lógica vive en
data_pipeline/extraction). Dagster solo aporta ejecución, logs y status; no
reimplementa la extracción. Ver ADR-011.
"""

from dagster import AssetExecutionContext, MaterializeResult, asset

from data_pipeline.extraction.extract_pozos import extract_pozos
from data_pipeline.extraction.extract_produccion import extract_produccion


@asset(group_name="bronze")
def bronze_pozos(context: AssetExecutionContext) -> MaterializeResult:
    """Listado de pozos crudo en Bronze (full refresh)."""
    archivo = extract_pozos()
    context.log.info(f"pozos → {archivo}")
    return MaterializeResult(metadata={"ruta": str(archivo)})


@asset(group_name="bronze")
def bronze_produccion(context: AssetExecutionContext) -> MaterializeResult:
    """Producción cruda en Bronze, particionada por anio/mes (full refresh)."""
    destino = extract_produccion()
    context.log.info(f"produccion → {destino}")
    return MaterializeResult(metadata={"ruta": str(destino)})
