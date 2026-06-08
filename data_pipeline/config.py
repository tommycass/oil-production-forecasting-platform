"""Configuración central del pipeline de datos (Fase 2).

Centraliza las URLs de las fuentes públicas de datos.gob.ar y las rutas locales
de la capa Bronze. Cualquier script de extracción importa desde acá, así las
fuentes se definen una sola vez.
"""

from pathlib import Path

# Raíz del repo (data_pipeline/ está un nivel debajo) y rutas de datos.
# El contenido de data/ está gitignoreado; solo se versiona la estructura.
#  - LANDING_DIR: descarga cruda completa (una sola por corrida), pre-Bronze.
#  - BRONZE_DIR:  crudo persistido (parquet); producción particionada por anio/mes.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LANDING_DIR = PROJECT_ROOT / "data" / "landing"
BRONZE_DIR = PROJECT_ROOT / "data" / "bronze"

# Fuentes datos.gob.ar — Producción de petróleo y gas por pozo (Capítulo IV).
# Cada entrada: URL de descarga directa del CSV + descripción de qué trae.
SOURCES = {
    "produccion": {
        "url": (
            "http://datos.energia.gob.ar/dataset/"
            "c846e79c-026c-4040-897f-1ad3543b407c/resource/"
            "b5b58cdc-9e07-41f9-b392-fb9ec68b0725/download/"
            "produccin-de-pozos-de-gas-y-petrleo-no-convencional.csv"
        ),
        "descripcion": (
            "Producción mensual por pozo (gas/petróleo no convencional). "
            "Grano: idpozo + anio + mes. Es la base de la fact table."
        ),
    },
    "pozos": {
        "url": (
            "http://datos.energia.gob.ar/dataset/"
            "c846e79c-026c-4040-897f-1ad3543b407c/resource/"
            "cbfa4d79-ffb3-4096-bab5-eb0dde9a8385/download/"
            "listado-de-pozos-cargados-por-empresa-operadora.csv"
        ),
        "descripcion": (
            "Catálogo de pozos cargados por empresa operadora. "
            "Clave: idpozo. Alimenta las dimensiones (pozo, operadora, yacimiento)."
        ),
    },
}
