"""Materialización del feature store (Fase 3, Rol 2) — handoff de Rol 1.

Reproduce **exactamente** las features del entrenamiento (las 29 del modelo campeón)
**reusando el código de `ml/`** (única fuente de verdad → cero training-serving skew):
las 7 features de ingeniería salen de `ml.features.add_engineered_features` y las listas
de columnas de `ml.dataset`. La única lógica propia es el ensamblado (lectura de Bronze,
universo, drop de negativos, merge del target) que replica `ml.dataset.build_basic_dataset`,
con una diferencia: el target se hace por **left-join** para conservar la última fila de
cada pozo (con `y_next` NULL) y que la API pueda inferir el mes siguiente.

**Dos modelos (ADR-042):** se materializa **una tabla por target** — petróleo
(`prod_pet`) en `feat_produccion_pozo_mensual` (nombre histórico) y gas (`prod_gas`)
en `feat_produccion_pozo_mensual_gas`. Cada tabla tiene su **universo** (pozos con ese
target > 0 en train), sus **features de ingeniería** sobre el target (`prod_pet_*` vs
`prod_gas_*`) y su `y_next`. Las 8 numéricas base y las 14 categóricas son compartidas
(se duplican entre tablas). Rol 3 lee la tabla del target que sirve.

Fuente: `bronze.produccion` (crudo, mismas columnas que el CSV que usa el training;
gobernado en el DW). Ver docs/feature-store.md (contrato) y ADR-036 (Revisión).

No importa dagster: la lógica es testeable aislada (paridad vs `build_basic_dataset`).
"""

from __future__ import annotations

import os

import pandas as pd
from sqlalchemy import create_engine, text

from ml import features as ml_features
from ml.config import TARGET, TARGETS, TRAIN_END
from ml.dataset import BASIC_CATEGORICAL_FEATURES, BASIC_NUMERIC_FEATURES

FEATURE_SCHEMA = "features"
FEATURE_TABLE = "feat_produccion_pozo_mensual"  # petróleo (nombre histórico)

# Columnas crudas a leer de Bronze: claves + las features base (numéricas + categóricas).
# `mes` está en BASIC_NUMERIC_FEATURES; se dedup con dict.fromkeys. Las mismas columnas
# crudas sirven a los dos targets (prod_pet y prod_gas están ambas en las numéricas base).
_RAW_COLS = list(dict.fromkeys(["idpozo", "anio", "mes", *BASIC_NUMERIC_FEATURES, *BASIC_CATEGORICAL_FEATURES]))


def table_for(target: str) -> str:
    """Tabla del store para un ``target``. Petróleo mantiene el nombre histórico
    (`feat_produccion_pozo_mensual`); gas (y futuros targets) llevan sufijo, siguiendo
    la convención `_gas` del pipeline de `ml/` (ADR-042): `feat_produccion_pozo_mensual_gas`.
    """
    return FEATURE_TABLE if target == "prod_pet" else f"{FEATURE_TABLE}_{target.removeprefix('prod_')}"


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


def build_store_features(df: pd.DataFrame, target: str = TARGET) -> pd.DataFrame:
    """Construye la tabla de features del store a partir del crudo de producción.

    Replica el ensamblado de `ml.dataset.build_basic_dataset` (tipado, universo
    train-only, drop de negativos, `mes` = mes del target, merge por calendario)
    reusando `ml.features.add_engineered_features` para las 7 features de ingeniería.
    Target por **left-join** (conserva la última fila de cada pozo, `y_next` NULL)
    para habilitar la inferencia del mes siguiente.

    ``target`` (``prod_pet`` por defecto / ``prod_gas``, ADR-042) cambia el **universo**
    (pozos con ese target > 0 en train), las **features de ingeniería autorregresivas**
    (`{target}_roll3/delta1/lag12/acum6`, `prod_vecinos_mean` sobre el target) y el
    `y_next` (= target del mes t+1). Las numéricas base y las categóricas no dependen
    del target. No se llama a `df.copy()` defensivo más de lo necesario para poder
    reusar el mismo crudo de Bronze en los dos targets sin recargar.
    """
    df = df.copy()
    # idpozo como entero (en Bronze viene texto): clave del store y del lookup de la API.
    df["idpozo"] = pd.to_numeric(df["idpozo"], errors="coerce").astype("int64")
    for c in BASIC_NUMERIC_FEATURES:  # incluye `mes`
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["anio"] = pd.to_numeric(df["anio"], errors="coerce")  # solo para construir periodo
    df["periodo"] = pd.to_datetime(dict(year=df.anio, month=df.mes, day=1))

    # universo train-only (anti-leakage de selección, ADR-031): pozos con `target`>0
    # en algún mes <= TRAIN_END. El universo gasífero es distinto (y más amplio) que el
    # petrolero (ADR-042).
    pozos = df.loc[(df[target] > 0) & (df.periodo <= TRAIN_END), "idpozo"].unique()
    df = df[df.idpozo.isin(pozos)].sort_values(["idpozo", "periodo"]).reset_index(drop=True)

    # descartar producción negativa (errores de dato, ADR-039) antes del feature eng.
    prod_cols = ["prod_pet", "prod_gas", "prod_agua"]
    df = df[~(df[prod_cols] < 0).any(axis=1)].reset_index(drop=True)

    # 7 features de ingeniería sobre el target: REUSO del código de Rol 1 (paridad garantizada).
    df = ml_features.add_engineered_features(df, target=target)

    # target = `target` del mes siguiente, alineado por calendario. LEFT para conservar
    # la última fila de cada pozo (y_next NULL) → la API puede predecir el mes siguiente.
    nxt = df[["idpozo", "periodo", target]].rename(columns={target: "y_next"})
    nxt["periodo"] = nxt["periodo"] - pd.DateOffset(months=1)
    out = df.merge(nxt, on=["idpozo", "periodo"], how="left")

    out["periodo_objetivo"] = out["periodo"] + pd.DateOffset(months=1)
    out["mes"] = out["periodo_objetivo"].dt.month  # mes del MES OBJETIVO (t+1), ADR-031

    # `add_prod_vecinos_mean` (ml/features) castea idpozo a object al hacer merge; lo
    # devolvemos a int64 para que el store tenga una clave limpia (lookup de la API).
    out["idpozo"] = out["idpozo"].astype("int64")

    cols = (
        ["idpozo", "periodo", "periodo_objetivo"]
        + BASIC_NUMERIC_FEATURES + ml_features.engineered_feature_names(target)
        + BASIC_CATEGORICAL_FEATURES + ["y_next"]
    )
    return out[cols].sort_values(["idpozo", "periodo"]).reset_index(drop=True)


def materializar(engine, target: str = TARGET) -> int:
    """Construye y escribe la tabla del store de un ``target`` en Postgres.
    Devuelve filas escritas. Para los dos modelos, ver `materializar_todos`."""
    df = build_store_features(leer_bronze(engine), target=target)
    with engine.begin() as conn:
        conn.execute(text(f"create schema if not exists {FEATURE_SCHEMA}"))
    df.to_sql(table_for(target), engine, schema=FEATURE_SCHEMA, if_exists="replace", index=False)
    return len(df)


def materializar_todos(engine, targets=TARGETS) -> dict[str, int]:
    """Materializa **una tabla por target** (petróleo + gas, ADR-042).

    Lee Bronze **una sola vez** (las columnas crudas son las mismas para ambos) y
    reescribe cada tabla con su universo/ingeniería/`y_next`. Devuelve `{target: filas}`.
    Es lo que invoca el job de retrain (`features_refrescadas`, ADR-041).
    """
    raw = leer_bronze(engine)
    with engine.begin() as conn:
        conn.execute(text(f"create schema if not exists {FEATURE_SCHEMA}"))
    filas: dict[str, int] = {}
    for target in targets:
        df = build_store_features(raw, target=target)
        df.to_sql(table_for(target), engine, schema=FEATURE_SCHEMA, if_exists="replace", index=False)
        filas[target] = len(df)
    return filas
