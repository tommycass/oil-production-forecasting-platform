"""Carga una MUESTRA de las fuentes reales a `bronze` (dev/test).

Permite probar el pipeline dbt (Silver/Gold/DQ) sin esperar la extracción completa
del Data Engineer: descarga las primeras N filas de cada CSV de datos.gob.ar y las
materializa en `bronze.produccion` / `bronze.pozos` como texto, con `fecha_ingesta`.

Reusa las URLs definidas una sola vez en data_pipeline/config.py (DRY).

Uso:
    python scripts/seed_sample_bronze.py --rows 2000
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import requests
from sqlalchemy import create_engine, text

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
from data_pipeline.config import SOURCES  # noqa: E402


def engine_from_env():
    url = (
        f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'oil')}:"
        f"{os.getenv('POSTGRES_PASSWORD', 'oil')}@"
        f"{os.getenv('POSTGRES_HOST', 'localhost')}:"
        f"{os.getenv('POSTGRES_PORT', '5432')}/"
        f"{os.getenv('POSTGRES_DB', 'oil_dw')}"
    )
    return create_engine(url)


def fetch_head(url: str, n_rows: int) -> pd.DataFrame:
    """Descarga solo las primeras n_rows+1 líneas (header + datos) por streaming."""
    lines: list[str] = []
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        r.encoding = "utf-8"
        for line in r.iter_lines(decode_unicode=True):
            lines.append(line)
            if len(lines) > n_rows:
                break
    return pd.read_csv(io.StringIO("\n".join(lines)), dtype=str)


def _texto_preservando_nulos(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte todo a texto pero deja los faltantes como None (→ SQL NULL).

    Igual que en load_bronze.py: un `.astype(str)` volvería los campos vacíos el
    literal "nan", que Silver no reconoce como vacío y rompería los casts a
    integer/date. Acá los nulos se preservan como None.
    """
    str_df = df.astype("string")  # valores → str, faltantes → <NA>
    return str_df.astype(object).where(str_df.notna(), None)


def seed(engine, fuente: str, url: str, n_rows: int) -> int:
    df = _texto_preservando_nulos(fetch_head(url, n_rows))
    df["fecha_ingesta"] = datetime.now(timezone.utc).replace(tzinfo=None)
    with engine.begin() as conn:
        conn.execute(text("create schema if not exists bronze"))
    df.to_sql(fuente, engine, schema="bronze", if_exists="replace", index=False)
    print(f"[seed] bronze.{fuente}: {len(df)} filas de muestra")
    return len(df)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=2000, help="filas de muestra por fuente")
    args = ap.parse_args()

    engine = engine_from_env()
    for fuente, meta in SOURCES.items():
        seed(engine, fuente, meta["url"], args.rows)


if __name__ == "__main__":
    main()
