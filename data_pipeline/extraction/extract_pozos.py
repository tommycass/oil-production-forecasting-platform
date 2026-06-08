"""Extracción de la fuente 'listado de pozos' → capa Bronze.

Estrategia de carga: FULL REFRESH. Es un catálogo chico (~84k filas) que se baja
entero en cada corrida. El crudo se persiste en parquet versionado por fecha de
ingesta (data/bronze/pozos/ingesta=AAAA-MM-DD/pozos.parquet), sin transformar:
la limpieza es responsabilidad de Silver (ver docs/data-model.md). Ver ADR-013.
"""

from datetime import date
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests

from data_pipeline.config import BRONZE_DIR, SOURCES

# Tiempo máximo de espera de la descarga (segundos) antes de abortar.
_HTTP_TIMEOUT = 60


def extract_pozos(ingesta: date | None = None) -> Path:
    """Descarga el listado de pozos y lo persiste crudo en la capa Bronze.

    Args:
        ingesta: fecha de ingesta a usar en el particionado. Por defecto, hoy.

    Returns:
        Ruta del archivo parquet escrito en Bronze.
    """
    ingesta = ingesta or date.today()
    url = SOURCES["pozos"]["url"]

    # 1. Descargar el CSV crudo desde datos.gob.ar.
    print(f"[pozos] descargando {url}")
    resp = requests.get(url, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()

    # 2. Leer tal cual viene: todo como texto, sin interpretar tipos ni nulos.
    #    En Bronze el dato es inmutable; el casteo se hace en Silver.
    #    encoding="utf-8-sig" descarta el BOM inicial del CSV de la fuente
    #    (si no, quedaría pegado al nombre de la primera columna).
    df = pd.read_csv(BytesIO(resp.content), dtype=str, encoding="utf-8-sig")

    # 3. Escribir parquet versionado por fecha de ingesta. Re-correr el mismo día
    #    sobrescribe el archivo del día (idempotente), no acumula duplicados.
    destino = BRONZE_DIR / "pozos" / f"ingesta={ingesta.isoformat()}"
    destino.mkdir(parents=True, exist_ok=True)
    archivo = destino / "pozos.parquet"
    df.to_parquet(archivo, engine="pyarrow", index=False)

    # 4. Reportar el resultado.
    print(f"[pozos] {len(df)} filas → {archivo}")
    return archivo


if __name__ == "__main__":
    extract_pozos()
