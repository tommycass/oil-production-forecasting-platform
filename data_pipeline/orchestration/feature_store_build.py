"""Materialización del feature store (Fase 3, Rol 2) — handoff de Rol 1.

Reproduce **exactamente** las features del entrenamiento (las 29 del modelo campeón)
**reusando el código de `ml/`** (única fuente de verdad → cero training-serving skew):
las 7 features de ingeniería salen de `ml.features.add_engineered_features` y las listas
de columnas de `ml.dataset`. La única lógica propia es el ensamblado (lectura de Bronze,
universo, drop de negativos, merge del target) que replica `ml.dataset.build_basic_dataset`,
con una diferencia: el target se hace por **left-join** para conservar la última fila de
cada pozo (con `y_next` NULL) y que la API pueda inferir el mes siguiente.

Fuente: `bronze.produccion` (crudo, mismas columnas que el CSV que usa el training;
gobernado en el DW). Ver docs/feature-store.md (contrato) y ADR-036 (Revisión).

No importa dagster: la lógica es testeable aislada (paridad vs `build_basic_dataset`).
"""

from __future__ import annotations

import os

import pandas as pd
from sqlalchemy import create_engine, text

from ml import features as ml_features
from ml.config import TARGET, TRAIN_END
from ml.dataset import BASIC_CATEGORICAL_FEATURES, BASIC_NUMERIC_FEATURES

FEATURE_SCHEMA = "features"
FEATURE_TABLE = "feat_produccion_pozo_mensual"

# Columnas crudas a leer de Bronze: claves + las features base (numéricas + categóricas).
# `mes` está en BASIC_NUMERIC_FEATURES; se dedup con dict.fromkeys.
_RAW_COLS = list(dict.fromkeys(["idpozo", "anio", "mes", *BASIC_NUMERIC_FEATURES, *BASIC_CATEGORICAL_FEATURES]))


def engine_from_env():
    url = (
        f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'oil')}:"
        f"{os.getenv('POSTGRES_PASSWORD', 'oil')}@{os.getenv('POSTGRES_HOST', 'localhost')}:"
        f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'oil_dw')}"
    )
    return create_engine(url)


def leer_bronze(engine) -> pd.DataFrame:
    """Lee las columnas crudas de `bronze.produccion` (todo texto)."""
    sel = ", ".join(f'"{c}"' for c in _RAW_COLS)
    return pd.read_sql(f"select {sel} from bronze.produccion", engine)


def build_store_features(df: pd.DataFrame) -> pd.DataFrame:
    """Construye la tabla de features del store a partir del crudo de producción.

    Replica el ensamblado de `ml.dataset.build_basic_dataset` (tipado, universo
    train-only, drop de negativos, `mes` = mes del target, merge por calendario)
    reusando `ml.features.add_engineered_features` para las 7 features de ingeniería.
    Target por **left-join** (conserva la última fila de cada pozo, `y_next` NULL)
    para habilitar la inferencia del mes siguiente.
    """
    df = df.copy()
    # idpozo como entero (en Bronze viene texto): clave del store y del lookup de la API.
    df["idpozo"] = pd.to_numeric(df["idpozo"], errors="coerce").astype("int64")
    for c in BASIC_NUMERIC_FEATURES:  # incluye `mes`
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["anio"] = pd.to_numeric(df["anio"], errors="coerce")  # solo para construir periodo
    df["periodo"] = pd.to_datetime(dict(year=df.anio, month=df.mes, day=1))

    # universo train-only (anti-leakage de selección, ADR-031): pozos con prod_pet>0
    # en algún mes <= TRAIN_END.
    pozos = df.loc[(df[TARGET] > 0) & (df.periodo <= TRAIN_END), "idpozo"].unique()
    df = df[df.idpozo.isin(pozos)].sort_values(["idpozo", "periodo"]).reset_index(drop=True)

    # descartar producción negativa (errores de dato, ADR-039) antes del feature eng.
    prod_cols = ["prod_pet", "prod_gas", "prod_agua"]
    df = df[~(df[prod_cols] < 0).any(axis=1)].reset_index(drop=True)

    # 7 features de ingeniería: REUSO del código de Rol 1 (paridad garantizada).
    df = ml_features.add_engineered_features(df)

    # target = prod_pet del mes siguiente, alineado por calendario. LEFT para conservar
    # la última fila de cada pozo (y_next NULL) → la API puede predecir el mes siguiente.
    nxt = df[["idpozo", "periodo", TARGET]].rename(columns={TARGET: "y_next"})
    nxt["periodo"] = nxt["periodo"] - pd.DateOffset(months=1)
    out = df.merge(nxt, on=["idpozo", "periodo"], how="left")

    out["periodo_objetivo"] = out["periodo"] + pd.DateOffset(months=1)
    out["mes"] = out["periodo_objetivo"].dt.month  # mes del MES OBJETIVO (t+1), ADR-031

    # `add_prod_vecinos_mean` (ml/features) castea idpozo a object al hacer merge; lo
    # devolvemos a int64 para que el store tenga una clave limpia (lookup de la API).
    out["idpozo"] = out["idpozo"].astype("int64")

    cols = (
        ["idpozo", "periodo", "periodo_objetivo"]
        + BASIC_NUMERIC_FEATURES + ml_features.ENGINEERED_FEATURES
        + BASIC_CATEGORICAL_FEATURES + ["y_next"]
    )
    return out[cols].sort_values(["idpozo", "periodo"]).reset_index(drop=True)


def materializar(engine) -> int:
    """Construye y escribe el feature store en Postgres. Devuelve filas escritas."""
    df = build_store_features(leer_bronze(engine))
    with engine.begin() as conn:
        conn.execute(text(f"create schema if not exists {FEATURE_SCHEMA}"))
    df.to_sql(FEATURE_TABLE, engine, schema=FEATURE_SCHEMA, if_exists="replace", index=False)
    return len(df)
