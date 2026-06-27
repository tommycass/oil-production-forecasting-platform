"""Carga de datos, construcción del target/features y split temporal.

Compartido por baseline.py y train.py para garantizar el mismo universo, el
mismo target y el mismo corte temporal (ADR-028).
"""
from __future__ import annotations
from pathlib import Path

import pandas as pd
from sklearn.preprocessing import OneHotEncoder

from ml.config import DATA_CSV, DATASET_BASICO_CSV, TARGET, TRAIN_END, VAL_END

_USECOLS = [
    "idpozo", "anio", "mes",
    "prod_pet", "prod_gas", "tef", "profundidad",
    "cuenca", "provincia", "formacion", "tipopozo",
]


def load_production(path=DATA_CSV) -> pd.DataFrame:
    """Carga la producción y la restringe al **universo petrolero** (pozos con
    al menos un mes de prod_pet > 0), ordenada por (pozo, periodo)."""
    df = pd.read_csv(path, usecols=_USECOLS, encoding="utf-8-sig")
    for c in ["prod_pet", "prod_gas", "tef", "profundidad"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["periodo"] = pd.to_datetime(dict(year=df.anio, month=df.mes, day=1))

    pozos_petroleros = df.loc[df.prod_pet > 0, "idpozo"].unique()
    df = (
        df[df.idpozo.isin(pozos_petroleros)]
        .sort_values(["idpozo", "periodo"])
        .reset_index(drop=True)
    )
    return df


def add_target(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega el target: prod_pet del **mes siguiente** (horizonte t+1)."""
    df["y_next"] = df.groupby("idpozo")[TARGET].shift(-1)
    return df


def add_baselines(df: pd.DataFrame) -> pd.DataFrame:
    """Predicciones de los baselines deterministas (ADR-029).
    Todas usan solo información hasta el mes t (anti-leakage)."""
    g = df.groupby("idpozo")[TARGET]
    df["b_persist"] = df[TARGET]                              # ŷ(t+1) = y(t)
    df["b_ma3"] = g.transform(lambda s: s.rolling(3).mean())  # media 3m
    df["b_seas"] = g.shift(11)                                # y(t-11): mismo mes año anterior
    return df


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Features autoregresivas validadas en el EDA (lags, media móvil,
    antigüedad, tef con lag). Disponibles al momento t (anti-leakage)."""
    g = df.groupby("idpozo")[TARGET]
    df["lag1"] = g.shift(1)
    df["lag2"] = g.shift(2)
    df["lag3"] = g.shift(3)
    df["roll3"] = g.transform(lambda s: s.rolling(3).mean())
    df["antiguedad"] = df.groupby("idpozo").cumcount()
    df["tef_lag1"] = df.groupby("idpozo")["tef"].shift(1)
    return df


def add_split(df: pd.DataFrame) -> pd.DataFrame:
    """Etiqueta cada fila como train / val / test según su periodo (ADR-028)."""
    split = pd.Series("train", index=df.index)
    split[(df.periodo > TRAIN_END) & (df.periodo <= VAL_END)] = "val"
    split[df.periodo > VAL_END] = "test"
    df["split"] = split
    return df


def build_modeling_frame() -> pd.DataFrame:
    """Pipeline completo: carga + target + baselines + features + split."""
    df = load_production()
    df = add_target(df)
    df = add_baselines(df)
    df = add_features(df)
    df = add_split(df)
    return df


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


def build_basic_dataset(path=DATA_CSV) -> pd.DataFrame:
    """Dataset básico para el forecast t+1, ya procesado **anti-leakage**.

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

    # universo petrolero definido SOLO con train (anti-leakage de selección):
    # pozos con prod_pet > 0 en algún mes <= TRAIN_END. Si se definiera sobre todo
    # el histórico, la pertenencia al universo usaría datos de val/test (un pozo que
    # recién produce petróleo en 2025 entraría también con sus filas de train).
    # Mismo criterio que ml.eda.load_train_raw. Ojo: NO descarta los meses en 0 de
    # los pozos petroleros (el pozo parado sigue siendo target válido = 0); solo deja
    # afuera pozos que nunca son petroleros (gas/inyección).
    pozos = df.loc[(df[TARGET] > 0) & (df.periodo <= TRAIN_END), "idpozo"].unique()
    df = (
        df[df.idpozo.isin(pozos)]
        .sort_values(["idpozo", "periodo"])
        .reset_index(drop=True)
    )

    # target = prod_pet del mes siguiente, alineado por calendario (no por fila)
    nxt = df[["idpozo", "periodo", TARGET]].rename(columns={TARGET: "y_next"})
    nxt["periodo"] = nxt["periodo"] - pd.DateOffset(months=1)
    out = df.merge(nxt, on=["idpozo", "periodo"], how="inner")

    out["periodo_objetivo"] = out["periodo"] + pd.DateOffset(months=1)

    # mes = mes del MES OBJETIVO (t+1). Es conocido de antemano (no es leakage) y
    # captura la estacionalidad del mes que se predice; a diferencia de las
    # medidas, que sí deben ser del mes t.
    out["mes"] = out["periodo_objetivo"].dt.month

    out = add_split(out)  # split por el mes de los features (periodo), ADR-028

    cols = (
        ["idpozo", "periodo", "periodo_objetivo", "split"]
        + BASIC_NUMERIC_FEATURES + BASIC_CATEGORICAL_FEATURES + ["y_next"]
    )
    return out[cols].sort_values(["idpozo", "periodo"]).reset_index(drop=True)


def save_basic_dataset(df: pd.DataFrame | None = None, path=DATASET_BASICO_CSV) -> Path:
    """Construye (si no se pasa ``df``) y guarda el dataset básico como CSV local.

    El destino está bajo ``data/`` y queda **gitignoreado** (data/.gitignore), así
    que no se versiona ni pisa el crudo original. Devuelve la ruta escrita.
    """
    if df is None:
        df = build_basic_dataset()
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
