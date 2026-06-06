"""Carga la capa Bronze (parquet) a PostgreSQL como texto.

Lee todos los .parquet bajo data/bronze/<fuente>/ y los materializa en el esquema
`bronze` del DW (tablas `bronze.produccion` y `bronze.pozos`). Las columnas se
cargan como TEXTO para preservar la fidelidad del crudo; el tipado y la limpieza
ocurren en Silver (modelos dbt).

Es el handoff Bronze → DW. En producción puede invocarlo el orquestador (Dagster,
Persona A) tras materializar Bronze. Idempotente: reemplaza la tabla destino.

Uso:
    python scripts/load_bronze.py                      # carga produccion y pozos
    python scripts/load_bronze.py --fuente produccion  # solo una fuente

Conexión vía env: POSTGRES_HOST/PORT/USER/PASSWORD/DB (defaults oil/oil/oil_dw).
"""
from __future__ import annotations

import argparse
import glob
import os
import re
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import create_engine, text

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BRONZE_DIR = os.path.join(REPO_ROOT, "data", "bronze")
FUENTES = ("produccion", "pozos")

# Fecha de ingesta codificada en el path de pozos (data/bronze/pozos/ingesta=YYYY-MM-DD/).
_INGESTA_RE = re.compile(r"ingesta=(\d{4}-\d{2}-\d{2})")


def _texto_preservando_nulos(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte todo a texto pero deja los faltantes como None (→ SQL NULL).

    El crudo de A trae los campos vacíos como NaN; un `.astype(str)` los volvería
    el literal "nan", que Silver no reconoce como vacío (su `nullif(..., '')` solo
    atrapa el string vacío) y rompería los casts a integer/date. Acá los nulos se
    preservan como None para que lleguen al DW como NULL.
    """
    str_df = df.astype("string")  # valores → str, faltantes → <NA>
    return str_df.astype(object).where(str_df.notna(), None)


def _fecha_ingesta_de(path: str, fallback: datetime) -> datetime:
    """Deriva la fecha de ingesta del path (`ingesta=YYYY-MM-DD`); si no está, fallback.

    Pozos es full-refresh versionado por fecha de ingesta: usar la fecha real del
    snapshot (no `now()`) hace determinístico el dedupe de silver_pozos, que se
    queda con el snapshot más reciente por idpozo.
    """
    m = _INGESTA_RE.search(path)
    return datetime.fromisoformat(m.group(1)) if m else fallback


def engine_from_env():
    url = (
        f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'oil')}:"
        f"{os.getenv('POSTGRES_PASSWORD', 'oil')}@"
        f"{os.getenv('POSTGRES_HOST', 'localhost')}:"
        f"{os.getenv('POSTGRES_PORT', '5432')}/"
        f"{os.getenv('POSTGRES_DB', 'oil_dw')}"
    )
    return create_engine(url)


def load_fuente(engine, fuente: str) -> int:
    pattern = os.path.join(BRONZE_DIR, fuente, "**", "*.parquet")
    files = sorted(glob.glob(pattern, recursive=True))
    if not files:
        print(f"[load_bronze] sin parquet para '{fuente}' en {pattern} — se omite")
        return 0

    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    frames = []
    for f in files:
        fdf = _texto_preservando_nulos(pd.read_parquet(f))
        fdf["fecha_ingesta"] = _fecha_ingesta_de(f, ahora)
        frames.append(fdf)
    df = pd.concat(frames, ignore_index=True)

    with engine.begin() as conn:
        conn.execute(text("create schema if not exists bronze"))
    df.to_sql(fuente, engine, schema="bronze", if_exists="replace", index=False)
    print(f"[load_bronze] bronze.{fuente}: {len(df)} filas desde {len(files)} parquet")
    return len(df)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fuente", choices=FUENTES, help="cargar una sola fuente")
    args = ap.parse_args()

    engine = engine_from_env()
    fuentes = (args.fuente,) if args.fuente else FUENTES
    for f in fuentes:
        load_fuente(engine, f)


if __name__ == "__main__":
    main()
