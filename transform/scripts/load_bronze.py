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
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import create_engine, text

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BRONZE_DIR = os.path.join(REPO_ROOT, "data", "bronze")
FUENTES = ("produccion", "pozos")


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

    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True).astype(str)
    df["fecha_ingesta"] = datetime.now(timezone.utc).replace(tzinfo=None)

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
