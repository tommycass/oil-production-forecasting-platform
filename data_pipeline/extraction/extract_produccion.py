"""Extracción de la fuente 'producción de pozos' → capa Bronze.

Estrategia de carga: FULL REFRESH a Bronze, particionado por `anio/mes`
(data/bronze/produccion/anio=YYYY/mes=MM/produccion.parquet). El particionado
es lo que habilita reprocesar un mes puntual (backfill) sin tocar el resto.

El crudo se persiste sin transformar (todo como texto); la limpieza y el
merge/upsert fino por (idpozo, anio, mes) —necesario porque la fuente corrige
meses ya publicados (columna `rectificado`)— corren aguas abajo en Silver/Gold.
Ver docs/data-model.md, ADR-012 y ADR-013.
"""

import shutil
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests

from data_pipeline.config import BRONZE_DIR, SOURCES

# La fuente es grande (~144 MB); damos más margen que en pozos.
_HTTP_TIMEOUT = 300


def extract_produccion() -> Path:
    """Descarga la producción de pozos y la persiste cruda en Bronze por anio/mes.

    Returns:
        Ruta del directorio Bronze de producción (raíz de las particiones).
    """
    url = SOURCES["produccion"]["url"]

    # 1. Descargar el CSV crudo completo desde datos.gob.ar.
    print(f"[produccion] descargando {url}")
    resp = requests.get(url, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()

    # 2. Leer tal cual viene: todo como texto. encoding="utf-8-sig" descarta el
    #    BOM inicial (la fuente lo trae, igual que el listado de pozos).
    df = pd.read_csv(BytesIO(resp.content), dtype=str, encoding="utf-8-sig")

    # 3. Full refresh idempotente: borrar las particiones previas antes de
    #    reescribir. Re-correr deja Bronze igual, sin acumular duplicados.
    destino = BRONZE_DIR / "produccion"
    if destino.exists():
        shutil.rmtree(destino)

    # 4. Particionar por anio/mes: una carpeta (y un parquet) por período.
    #    dropna=False para no descartar filas si faltara anio/mes (Bronze fiel).
    grupos = df.groupby(["anio", "mes"], dropna=False)
    for (anio, mes), grupo in grupos:
        particion = destino / f"anio={anio}" / f"mes={mes}"
        particion.mkdir(parents=True, exist_ok=True)
        grupo.to_parquet(particion / "produccion.parquet", engine="pyarrow", index=False)

    # 5. Reportar el resultado.
    print(f"[produccion] {len(df)} filas en {grupos.ngroups} particiones → {destino}")
    return destino


if __name__ == "__main__":
    extract_produccion()
