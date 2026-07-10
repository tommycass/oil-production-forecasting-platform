"""Carga de datos, construcción del target/features y split temporal.

Compartido por baseline.py y train.py para garantizar el mismo universo, el
mismo target y el mismo corte temporal (ADR-028).
"""
from __future__ import annotations
import os
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine
from sklearn.preprocessing import OneHotEncoder

from ml import features
from ml.config import (
    DATA_CSV,
    DATASET_BASICO_CSV,
    FEATURE_STORE_SCHEMA,
    FEATURE_STORE_TABLE,
    TARGET,
    retrain_asof,
    split_bounds,
)

def add_split(df: pd.DataFrame, anchor=None) -> pd.DataFrame:
    """Etiqueta cada fila como train / val / test según su periodo (ADR-028).

    Los cortes se derivan del ``anchor`` (última fecha observada) vía ``split_bounds``.
    Pasar ``anchor`` explícito si el ``df`` ya se filtró (p. ej. sin la fila de
    inferencia); si no, se usa ``df.periodo.max()``."""
    if anchor is None:
        anchor = df["periodo"].max()
    train_end, val_end = split_bounds(anchor)
    split = pd.Series("train", index=df.index)
    split[(df.periodo > train_end) & (df.periodo <= val_end)] = "val"
    split[df.periodo > val_end] = "test"
    df["split"] = split
    return df


# --- FEATURE STORE como fuente de entrenamiento ------------
# El training/evaluación/baselines leen la tabla del store por target — la MISMA que
# sirve la inferencia (ADR-041). Cero training-serving skew por construcción y sin
# paths locales: el store ya trae las features finales + `y_next`, no se re-hace nada.

def _store_engine():
    """Engine del DW (Postgres) desde las env vars ``POSTGRES_*`` (mismas que dbt / la
    API / el feature store). En local apunta al container ``postgres`` del compose."""
    url = (
        f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'oil')}:"
        f"{os.getenv('POSTGRES_PASSWORD', 'oil')}@{os.getenv('POSTGRES_HOST', 'localhost')}:"
        f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'oil_dw')}"
    )
    return create_engine(url)


def build_dataset_from_store(target: str = TARGET) -> pd.DataFrame:
    """Dataset de entrenamiento leído del **feature store** (fuente única de verdad).

    Lee ``features.feat_produccion_pozo_mensual[_gas]`` (según ``target``), que ya trae
    las **features seleccionadas** del modelo (27 petróleo / 19 gas, ADR-041) + ``y_next``,
    materializadas desde Bronze con el mismo código de ``ml/`` (paridad validada). Como es
    exactamente la tabla que consume la inferencia, entrenar sobre ella garantiza **cero
    training-serving skew** y elimina el CSV local.

    Ajustes de lectura, equivalentes a lo que hacía el assembly desde CSV:
    - **Filtra ``y_next`` no nulo:** el store conserva la última fila de cada pozo con
      ``y_next`` NULL (para que la API infiera el mes siguiente); esas filas no son
      ejemplos de entrenamiento y se descartan (equivale al inner-join del target).
    - **Deriva el ``split`` temporal** por ``periodo`` (ADR-028), que el store no persiste.

    El corte por fecha del reproceso (``RETRAIN_ASOF``, ADR-040) se aplica al
    **materializar** el store, no acá: el retrain re-materializa el store recortado y
    luego entrena leyéndolo.
    """
    tabla = f"{FEATURE_STORE_SCHEMA}.{FEATURE_STORE_TABLE[target]}"
    df = pd.read_sql(f"select * from {tabla}", _store_engine())
    df["periodo"] = pd.to_datetime(df["periodo"])
    df["periodo_objetivo"] = pd.to_datetime(df["periodo_objetivo"])
    # anchor del store completo (incluye la fila de inferencia y_next NULL), tomado ANTES
    # de descartarla: alinea el split con el universo del store, sin off-by-one.
    anchor = df["periodo"].max()
    df = df[df["y_next"].notna()].reset_index(drop=True)
    df = add_split(df, anchor=anchor)
    return df.sort_values(["idpozo", "periodo"]).reset_index(drop=True)


# --- Dataset básico ya procesado (anti-leakage, features del mes anterior) ----
# Features candidatas que salieron del EDA (notebook 03, sección 7: las columnas
# con uso_modelo == "input"), más prod_pet, que entra como feature porque al
# desfasar features (mes t) y target (mes t+1) representa la "producción de
# petróleo del mes anterior". La lista se mantiene acá (no se importa de ml.eda)
# para no arrastrar matplotlib a la pipeline.
BASIC_NUMERIC_FEATURES = [
    "prod_pet", "prod_gas", "prod_agua", "tef",
    "profundidad", "coordenadax", "coordenaday",
    "mes",
]
# Nota: `anio` NO entra como feature. Sus valores en val/test (2024–2026) caen
# fuera del rango de train (≤2023) → extrapolación (sobre todo en árboles, que no
# extrapolan), y la tendencia que aportaría ya la captura el lag de prod_pet. Se
# carga igual para construir `periodo` y derivar el mes objetivo. `mes` sí entra:
# es cíclico y sus valores (1–12) están todos cubiertos por train.
BASIC_CATEGORICAL_FEATURES = [
    "tipoextraccion", "tipoestado", "tipopozo",
    "empresa", "formprod", "formacion",
    "areapermisoconcesion", "areayacimiento",
    "cuenca", "provincia", "proyecto",
    "clasificacion", "subclasificacion", "sub_tipo_recurso",
]


def build_basic_dataset(
    path=DATA_CSV, target: str = TARGET, asof=None
) -> pd.DataFrame:
    """Dataset básico para el forecast t+1, ya procesado **anti-leakage**.

    ``target`` elige qué se predice: ``prod_pet`` (petróleo, default) o ``prod_gas``
    (gas, ADR-039). Cambia el **universo** (pozos con ese ``target`` > 0 en train),
    el **target** (``y_next`` = ``target`` del mes t+1) y las **features de ingeniería
    autorregresivas** (calculadas sobre ``target``). Las features crudas
    (``prod_pet``, ``prod_gas``, ``prod_agua``, …) se mantienen para ambos: para el
    modelo de gas, ``prod_gas(t)`` es la señal de persistencia y ``prod_pet(t)`` entra
    como señal cruzada (ambas son del mes t, no del futuro → sin leakage).

    ``asof`` recorta el dataset a ``periodo <= asof`` (reproceso "como si fuera el día
    X", ADR-040): así el reentreno de una fecha pasada **no usa datos posteriores**
    (anti-leakage del backfill). Si es ``None``, se toma de la env var ``RETRAIN_ASOF``
    (``ml.config.retrain_asof``); si tampoco está, no recorta. El recorte se aplica
    **antes** de definir el universo y las features, así todo respeta el corte.

    Cada fila es ``(pozo, mes t)``: las **medidas son del mes t** y el target
    (``y_next``) es la producción de petróleo del **mes siguiente (t+1)**. Por
    construcción ninguna medida usa información del mes que se predice, y
    ``prod_pet`` del mes t queda como "producción de petróleo del mes anterior".

    **Calendario: ``mes`` corresponde al mes objetivo (t+1)**, no al mes de las
    medidas. La fecha del mes a predecir se conoce de antemano (no es leakage) y
    es la señal útil de estacionalidad del mes que se pronostica. ``anio`` **no**
    es feature (extrapolación fuera del rango de train); solo se usa para derivar
    ``periodo``.

    El target se arma por **merge de calendario** (``periodo + 1 mes``), no por
    ``shift(-1)`` de filas: si un pozo tiene un hueco en su serie mensual, no se
    inventa un par (features t, target t+k) con k != 1, simplemente esa fila se
    descarta (inner join).

    El **universo petrolero se define solo con train** (pozos con prod_pet > 0 en
    algún mes <= TRAIN_END), para que la selección de pozos no use datos de
    val/test. Split por ``periodo`` (ADR-028).
    """
    feats = BASIC_NUMERIC_FEATURES + BASIC_CATEGORICAL_FEATURES
    # anio se incluye en la carga (no es feature) porque hace falta para periodo
    usecols = list(dict.fromkeys(["idpozo", "anio", "mes", *feats]))
    df = pd.read_csv(path, usecols=usecols, encoding="utf-8-sig", low_memory=False)

    for c in BASIC_NUMERIC_FEATURES:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["anio"] = pd.to_numeric(df["anio"], errors="coerce")  # solo para construir periodo
    df["periodo"] = pd.to_datetime(dict(year=df.anio, month=df.mes, day=1))

    # reproceso por fecha (ADR-040): recortar a periodo <= asof ANTES de definir el
    # universo y las features, para no usar datos posteriores al reentrenar una fecha
    # pasada (anti-leakage del backfill). asof explícito > env var RETRAIN_ASOF.
    asof = retrain_asof() if asof is None else pd.Timestamp(asof)
    if asof is not None:
        df = df[df.periodo <= asof].reset_index(drop=True)

    # anchor tras el recorte asof: universo y split comparten la misma referencia (ADR-028).
    anchor = df["periodo"].max()
    train_end, _ = split_bounds(anchor)

    # universo petrolero definido SOLO con train (anti-leakage de selección):
    # pozos con prod_pet > 0 en algún mes <= train_end. Si se definiera sobre todo
    # el histórico, la pertenencia al universo usaría datos de val/test (un pozo que
    # recién produce petróleo en 2025 entraría también con sus filas de train).
    # Mismo criterio que ml.eda.load_train_raw. Ojo: NO descarta los meses en 0 de
    # los pozos petroleros (el pozo parado sigue siendo target válido = 0); solo deja
    # afuera pozos que nunca son petroleros (gas/inyección).
    pozos = df.loc[(df[target] > 0) & (df.periodo <= train_end), "idpozo"].unique()
    df = (
        df[df.idpozo.isin(pozos)]
        .sort_values(["idpozo", "periodo"])
        .reset_index(drop=True)
    )

    # descartar meses con producción negativa: son errores de dato (la producción
    # es físicamente >= 0). Se quitan ANTES del feature engineering para que no
    # contaminen lags/ventanas, y como el target sale de prod_pet del mes siguiente,
    # un mes negativo descartado tampoco puede ser target (el merge no lo encuentra).
    prod_cols = ["prod_pet", "prod_gas", "prod_agua"]
    negativos = (df[prod_cols] < 0).any(axis=1)
    if negativos.any():
        df = df[~negativos].reset_index(drop=True)

    # features de ingeniería sobre TODA la historia observada (antes del target,
    # para que los lags por calendario sean correctos). Ver ml/features.py.
    df = features.add_engineered_features(df, target=target)

    # target = `target` del mes siguiente, alineado por calendario (no por fila)
    nxt = df[["idpozo", "periodo", target]].rename(columns={target: "y_next"})
    nxt["periodo"] = nxt["periodo"] - pd.DateOffset(months=1)
    out = df.merge(nxt, on=["idpozo", "periodo"], how="inner")

    out["periodo_objetivo"] = out["periodo"] + pd.DateOffset(months=1)

    # mes = mes del MES OBJETIVO (t+1). Es conocido de antemano (no es leakage) y
    # captura la estacionalidad del mes que se predice; a diferencia de las
    # medidas, que sí deben ser del mes t.
    out["mes"] = out["periodo_objetivo"].dt.month

    out = add_split(out, anchor=anchor)  # split por el mes de los features (periodo), ADR-028

    cols = (
        ["idpozo", "periodo", "periodo_objetivo", "split"]
        + BASIC_NUMERIC_FEATURES + features.engineered_feature_names(target)
        + BASIC_CATEGORICAL_FEATURES + ["y_next"]
    )
    return out[cols].sort_values(["idpozo", "periodo"]).reset_index(drop=True)


def save_basic_dataset(
    df: pd.DataFrame | None = None, path=DATASET_BASICO_CSV, target: str = TARGET
) -> Path:
    """Construye (si no se pasa ``df``) y guarda el dataset básico como CSV local.

    El destino está bajo ``data/`` y queda **gitignoreado** (data/.gitignore), así
    que no se versiona ni pisa el crudo original. Devuelve la ruta escrita.
    """
    if df is None:
        df = build_basic_dataset(target=target)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


# --- One-hot encoding de las categóricas (anti-leakage: se ajusta en train) ---
# Valor con el que se imputan los nulls de las categóricas antes de encodear
# (lo sugerido en el EDA: una categoría explícita "DESCONOCIDO").
CATEGORICAL_NA_FILL = "DESCONOCIDO"


def fit_onehot_encoder(
    df: pd.DataFrame, cols: list[str] = BASIC_CATEGORICAL_FEATURES
) -> OneHotEncoder:
    """Ajusta un ``OneHotEncoder`` usando **solo las filas de train** (anti-leakage).

    Las categorías (y por ende las columnas resultantes) se aprenden únicamente
    de train. Además, **cada feature lleva una categoría explícita
    ``CATEGORICAL_NA_FILL`` ("DESCONOCIDO")** como fallback: a esa columna se
    mapean los nulls y, en ``transform``, cualquier categoría no vista en train
    (p. ej. una empresa nueva que aparece en val/test). Así una categoría
    desconocida no rompe el modelo ni queda en un todo-cero implícito, sino que
    se marca de forma explícita e inspeccionable.

    El fallback se aprende de train donde haya nulls (p. ej. ``tipoestado``); en
    features sin nulls en train su columna queda en 0 sobre train y solo se
    activa con desconocidos posteriores. No se crean columnas a partir de
    categorías del futuro (anti-leakage).

    Requiere que ``df`` tenga la columna ``split`` (la crea ``build_basic_dataset``).
    """
    train = df.loc[df["split"] == "train", cols].fillna(CATEGORICAL_NA_FILL)
    # vocabulario = categorías de train + "DESCONOCIDO" garantizado por feature
    categories = []
    for c in cols:
        cats = sorted(train[c].unique().tolist())
        if CATEGORICAL_NA_FILL not in cats:
            cats.append(CATEGORICAL_NA_FILL)
        categories.append(cats)
    enc = OneHotEncoder(
        categories=categories, handle_unknown="ignore", sparse_output=False, dtype="uint8"
    )
    enc.fit(train)
    return enc


def transform_onehot(
    df: pd.DataFrame, encoder: OneHotEncoder, cols: list[str] = BASIC_CATEGORICAL_FEATURES
) -> pd.DataFrame:
    """Aplica un encoder ya ajustado y devuelve un DataFrame de dummies 0/1.

    Las columnas son las que el encoder fijó en el fit (categorías de train +
    ``DESCONOCIDO``), con nombres tipo ``tipopozo_Petrolífero``, alineadas al
    índice de ``df``. Los nulls y las **categorías no vistas en train** se
    mapean a la columna ``<feature>_DESCONOCIDO`` (fallback explícito).
    """
    X = df[cols].copy()
    for i, c in enumerate(cols):
        conocidas = set(encoder.categories_[i])
        X[c] = X[c].where(X[c].isin(conocidas), CATEGORICAL_NA_FILL)
    mat = encoder.transform(X)
    names = encoder.get_feature_names_out(cols)
    return pd.DataFrame(mat, columns=names, index=df.index)


def onehot_encode_dataset(
    df: pd.DataFrame, cols: list[str] = BASIC_CATEGORICAL_FEATURES
) -> tuple[pd.DataFrame, OneHotEncoder]:
    """Pipeline de one-hot: ajusta en train + transforma todo ``df``.

    Reemplaza las columnas categóricas por sus columnas one-hot (deja claves,
    numéricas, ``split`` y ``y_next`` intactas, con ``y_next`` al final).
    Devuelve ``(df_encoded, encoder)``; el encoder se puede reusar para
    transformar nuevos datos con las mismas columnas.
    """
    enc = fit_onehot_encoder(df, cols)
    dummies = transform_onehot(df, enc, cols)
    base = df.drop(columns=[*cols, "y_next"])
    out = pd.concat([base, dummies, df["y_next"]], axis=1)
    return out, enc
