"""Feature engineering para el forecast de ``prod_pet`` a t+1 (Fase 3).

Funciones **modulares, una por feature**, que se agregan sobre el panel mensual
``(pozo, mes)``. Se llaman **antes del merge del target** (sobre toda la historia
observada de cada pozo) para que los lags por calendario sean correctos.

Anti-leakage:
- **Futuro→pasado:** toda feature usa solo datos del mes ``t`` o anteriores. Los
  lags y ventanas se calculan **por calendario** (``periodo - k meses``), no con
  ``shift`` de filas, así un hueco en la serie no "corre" el lag.
- **Val→train:** ninguna feature ajusta parámetros sobre los datos (no hay
  scalers, encoders ni medias globales aprendidas), así que no hay forma de que
  información de val/test entre al cálculo de una fila de train. El valor de una
  fila del mes ``t`` depende solo de datos de ese mismo ``t`` (o anteriores).
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors

def engineered_feature_names(target: str = "prod_pet") -> list[str]:
    """Nombres (en orden de salida) de las 7 features de ingeniería para un
    ``target`` dado (``prod_pet`` o ``prod_gas``, ADR-042).

    Las **4 features autorregresivas del target** llevan su prefijo
    (``{target}_roll3``, ``{target}_delta1``, ``{target}_lag12``,
    ``{target}_acum6``); ``water_cut``, ``produjo_mes_pasado`` y
    ``prod_vecinos_mean`` son **genéricas** (mismo nombre para petróleo y gas: cada
    modelo tiene su propio dataset, así que no colisionan). Para ``prod_pet``
    reproduce **exactamente** los nombres históricos.
    """
    return [
        f"{target}_roll3",
        f"{target}_delta1",
        f"{target}_lag12",
        f"{target}_acum6",
        "water_cut",
        "produjo_mes_pasado",
        "prod_vecinos_mean",
    ]


# Lista por defecto (target petróleo), para compatibilidad con los imports
# existentes (``features.ENGINEERED_FEATURES``, p. ej. en feature_store_build).
ENGINEERED_FEATURES = engineered_feature_names("prod_pet")


def _calendar_lag(df: pd.DataFrame, col: str, months: int) -> np.ndarray:
    """Valor de ``col`` en ``periodo - months`` para el mismo pozo, alineado por
    calendario (NaN si ese mes no existe en la serie del pozo).

    Vectorizado vía merge: se toma ``col`` y se le suma ``months`` al ``periodo``,
    de modo que cada fila ``(pozo, t)`` recupera el valor de ``(pozo, t-months)``.
    No usa ``shift`` de filas, así los huecos no desplazan el lag.
    """
    src = df[["idpozo", "periodo", col]].rename(columns={col: "_lagval"})
    src["periodo"] = src["periodo"] + pd.DateOffset(months=months)
    merged = df[["idpozo", "periodo"]].merge(src, on=["idpozo", "periodo"], how="left")
    return merged["_lagval"].to_numpy()


def add_prod_pet_roll3(df: pd.DataFrame, col: str = "prod_pet") -> pd.DataFrame:
    """Media móvil de ``col`` en {t, t-1, t-2}: nivel reciente suavizado.

    Genera la columna ``{col}_roll3`` (``prod_pet_roll3`` / ``prod_gas_roll3``)."""
    lags = pd.DataFrame({k: _calendar_lag(df, col, k) for k in (0, 1, 2)})
    df[f"{col}_roll3"] = lags.mean(axis=1)  # skipna: usa los meses disponibles
    return df


def add_prod_pet_delta1(df: pd.DataFrame, col: str = "prod_pet") -> pd.DataFrame:
    """Variación mes a mes ``col(t) - col(t-1)``: declinación reciente.

    Genera la columna ``{col}_delta1``."""
    df[f"{col}_delta1"] = df[col].to_numpy() - _calendar_lag(df, col, 1)
    return df


def add_prod_pet_lag12(df: pd.DataFrame, col: str = "prod_pet") -> pd.DataFrame:
    """``col`` 12 meses antes (mismo mes del año previo): estacionalidad anual.

    Genera la columna ``{col}_lag12``."""
    df[f"{col}_lag12"] = _calendar_lag(df, col, 12)
    return df


def add_prod_pet_acum6(df: pd.DataFrame, col: str = "prod_pet", window: int = 6) -> pd.DataFrame:
    """Producción acumulada en la ventana de los últimos ``window`` meses {t..t-5}.

    Genera la columna ``{col}_acum6``."""
    lags = pd.DataFrame({k: _calendar_lag(df, col, k) for k in range(window)})
    df[f"{col}_acum6"] = lags.sum(axis=1, min_count=1)  # NaN solo si no hay ningún mes
    return df


def add_water_cut(df: pd.DataFrame) -> pd.DataFrame:
    """Corte de agua del mes t: ``agua / (agua + petróleo)`` (madurez del pozo).

    Indicador físico: sube a medida que el pozo se agota. Si no produjo nada
    (denominador 0) se define 0.
    """
    agua = df["prod_agua"].to_numpy()
    denom = agua + df["prod_pet"].to_numpy()
    df["water_cut"] = np.divide(agua, denom, out=np.zeros_like(agua, dtype=float), where=denom > 0)
    return df


def add_produjo_mes_pasado(df: pd.DataFrame, col: str = "prod_pet") -> pd.DataFrame:
    """1 si el pozo produjo (``col`` > 0) el mes t (el "mes pasado" relativo al
    target). La columna se llama ``produjo_mes_pasado`` (genérica)."""
    df["produjo_mes_pasado"] = (df[col].to_numpy() > 0).astype("uint8")
    return df


def add_prod_vecinos_mean(df: pd.DataFrame, col: str = "prod_pet", k: int = 5) -> pd.DataFrame:
    """Media de ``col`` en el mes t de los ``k`` pozos más cercanos por
    coordenadas, excluyendo el propio pozo: dinámica del área / reservorio. La
    columna se llama ``prod_vecinos_mean`` (genérica; para gas promedia gas).

    Anti-leakage: usa la producción de los vecinos del **mismo mes t** (≤ t); el
    conjunto de vecinos se define con coordenadas estáticas (no con el target).
    """
    coords = df.groupby("idpozo")[["coordenadax", "coordenaday"]].first().sort_index()
    pozos = coords.index.to_numpy()
    k_eff = min(k + 1, len(pozos))  # +1 porque el vecino más cercano es uno mismo

    nn = NearestNeighbors(n_neighbors=k_eff).fit(coords.to_numpy())
    _, idx = nn.kneighbors(coords.to_numpy())
    neigh = idx[:, 1:]  # descarta la columna 0 (el propio pozo, distancia 0)

    # pivot mes × pozo de col; para cada (mes, pozo) promedio de sus vecinos
    piv = df.pivot_table(index="periodo", columns="idpozo", values=col, aggfunc="first")
    piv = piv.reindex(columns=pozos)
    valores = piv.to_numpy()  # (n_meses, n_pozos)
    with warnings.catch_warnings():  # nanmean sobre meses sin ningún vecino -> NaN (ok)
        warnings.simplefilter("ignore", category=RuntimeWarning)
        media_vecinos = np.nanmean(valores[:, neigh], axis=2)  # (n_meses, n_pozos)

    largo = (
        pd.DataFrame(media_vecinos, index=piv.index, columns=pozos)
        .reset_index()
        .melt(id_vars="periodo", var_name="idpozo", value_name="prod_vecinos_mean")
    )
    return df.merge(largo, on=["idpozo", "periodo"], how="left")


def add_engineered_features(
    df: pd.DataFrame, target: str = "prod_pet", k_vecinos: int = 5
) -> pd.DataFrame:
    """Agrega las 7 features de ingeniería sobre el panel mensual ``(pozo, mes)``,
    para el ``target`` dado (``prod_pet`` por defecto; ``prod_gas`` para el modelo
    de gas, ADR-042).

    Las 4 features autorregresivas (roll3/delta1/lag12/acum6) y los vecinos se
    calculan sobre la columna ``target``; ``water_cut`` es físico (agua/(agua+pet))
    y no depende del target. Debe llamarse con **toda la historia observada por
    pozo** (antes del merge del target) para que los lags por calendario sean
    correctos. Devuelve el ``df`` con las columnas de
    ``engineered_feature_names(target)`` agregadas.
    """
    df = df.sort_values(["idpozo", "periodo"]).reset_index(drop=True)
    df = add_prod_pet_roll3(df, col=target)
    df = add_prod_pet_delta1(df, col=target)
    df = add_prod_pet_lag12(df, col=target)
    df = add_prod_pet_acum6(df, col=target)
    df = add_water_cut(df)
    df = add_produjo_mes_pasado(df, col=target)
    df = add_prod_vecinos_mean(df, col=target, k=k_vecinos)
    return df
