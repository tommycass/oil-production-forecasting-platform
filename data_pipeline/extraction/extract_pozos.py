"""Extracción de la fuente 'listado de pozos' → capa Bronze.

Estrategia de carga: FULL REFRESH. Es un catálogo chico (~84k filas) que se baja
entero en cada corrida; reemplaza la versión anterior en Bronze. Ver ADR-012.

TODO (Fase 2): implementar descarga y persistencia cruda en Bronze de forma
idempotente (re-correr no duplica) y versionada por fecha de ingesta.
"""

from data_pipeline.config import SOURCES


def extract_pozos() -> None:
    """Descarga el listado de pozos y lo persiste crudo en la capa Bronze."""
    _ = SOURCES["pozos"]["url"]
    raise NotImplementedError("Pendiente: extracción de listado de pozos")


if __name__ == "__main__":
    extract_pozos()
