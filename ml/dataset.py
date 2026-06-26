"""Carga de datos, construcción del target/features y split temporal.

Compartido por baseline.py y train.py para garantizar el mismo universo, el
mismo target y el mismo corte temporal (ADR-028).
"""
from __future__ import annotations
import pandas as pd

from ml.config import DATA_CSV, TARGET, TRAIN_END, VAL_END

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
