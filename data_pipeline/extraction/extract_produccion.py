"""Extracción de la fuente 'producción de pozos' → landing + capa Bronze.

El flujo se parte en dos para habilitar el backfill por mes sin re-descargar:

1. `descargar_landing()`: baja el CSV completo (~144 MB) UNA vez y lo guarda crudo
   en `data/landing/produccion/`. La fuente solo publica el archivo entero.
2. `escribir_particion(anio, mes)`: lee el landing, filtra ese mes y escribe su
   partición en Bronze (`data/bronze/produccion/anio=YYYY/mes=MM/`). Reprocesar un
   mes corregido = reescribir solo esa partición, sin tocar el resto ni volver a
   descargar.

El crudo se persiste sin transformar (todo como texto); la limpieza y el
merge/upsert fino por (idpozo, anio, mes) corren aguas abajo en Silver/Gold.
Ver docs/data-model.md, ADR-012 y ADR-013.
"""

import shutil
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests

from data_pipeline.config import BRONZE_DIR, LANDING_DIR, SOURCES
from data_pipeline.extraction.validation import validar_columnas

# La fuente es grande (~144 MB); damos más margen que en pozos.
_HTTP_TIMEOUT = 300

# Archivo crudo completo (todos los meses juntos) del que se derivan las particiones.
_LANDING_FILE = LANDING_DIR / "produccion" / "produccion.parquet"


def descargar_landing() -> Path:
    """Descarga el CSV completo de producción y lo guarda crudo en landing.

    Una sola descarga por corrida; las particiones de Bronze se derivan de acá.

    Returns:
        Ruta del parquet de landing.
    """
    url = SOURCES["produccion"]["url"]
    print(f"[produccion] descargando {url}")
    resp = requests.get(url, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()

    # Todo como texto; encoding="utf-8-sig" descarta el BOM inicial de la fuente.
    df = pd.read_csv(BytesIO(resp.content), dtype=str, encoding="utf-8-sig")

    _LANDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_LANDING_FILE, engine="pyarrow", index=False)
    print(f"[produccion] landing: {len(df)} filas → {_LANDING_FILE}")
    return _LANDING_FILE


def escribir_particion(anio: str, mes: str, df: pd.DataFrame | None = None) -> Path:
    """Escribe en Bronze la partición de un (anio, mes) leyendo del landing.

    Idempotente por partición: reescribe solo ese mes, sin tocar los demás ni
    re-descargar. Si `df` ya viene cargado (corrida full), evita releer el landing.

    Args:
        anio: año del período a escribir.
        mes: mes del período a escribir.
        df: landing ya cargado (opcional, para reusar en corridas completas).

    Returns:
        Ruta del parquet de la partición escrita.
    """
    if df is None:
        df = pd.read_parquet(_LANDING_FILE)

    # Todo es texto en Bronze: comparar como string.
    mes_df = df[(df["anio"] == str(anio)) & (df["mes"] == str(mes))]

    particion = BRONZE_DIR / "produccion" / f"anio={anio}" / f"mes={mes}"
    particion.mkdir(parents=True, exist_ok=True)
    archivo = particion / "produccion.parquet"
    mes_df.to_parquet(archivo, engine="pyarrow", index=False)
    print(f"[produccion] partición {anio}-{mes}: {len(mes_df)} filas → {archivo}")
    return archivo


def extract_produccion_full() -> Path:
    """Descarga el landing y materializa todas las particiones presentes.

    Conveniencia para correr el flujo completo sin orquestador (smoke test).

    Returns:
        Ruta del directorio Bronze de producción (raíz de las particiones).
    """
    descargar_landing()
    df = pd.read_parquet(_LANDING_FILE)

    destino = BRONZE_DIR / "produccion"
    if destino.exists():
        shutil.rmtree(destino)

    for anio, mes in df.groupby(["anio", "mes"], dropna=False).groups:
        escribir_particion(anio, mes, df=df)

    print(f"[produccion] {len(df)} filas en {df.groupby(['anio', 'mes']).ngroups} particiones → {destino}")
    return destino


if __name__ == "__main__":
    extract_produccion_full()
