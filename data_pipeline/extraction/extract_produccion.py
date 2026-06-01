"""Extracción de la fuente 'producción de pozos' → capa Bronze.

Estrategia de carga: MERGE/UPSERT por (idpozo, anio, mes). La fuente corrige
meses ya publicados (columna `rectificado`), así que append puro duplicaría
datos. Particionado de Bronze sugerido por anio/mes para reprocesar un mes
puntual sin tocar el resto. Ver ADR-012 y ADR-013.

TODO (Fase 2): implementar descarga y persistencia cruda en Bronze de forma
idempotente y versionada por fecha de ingesta.
"""

from data_pipeline.config import SOURCES


def extract_produccion() -> None:
    """Descarga la producción de pozos y la persiste cruda en la capa Bronze."""
    _ = SOURCES["produccion"]["url"]
    raise NotImplementedError("Pendiente: extracción de producción de pozos")


if __name__ == "__main__":
    extract_produccion()
