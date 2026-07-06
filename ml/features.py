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
    """Nombres (en orden de salida) de las features de ingeniería para un
    ``target`` dado (``prod_pet`` o ``prod_gas``, ADR-042).

    Bloques:
    - **Históricas (7):** las 4 autorregresivas con prefijo del target
      (``{target}_roll3/delta1/lag12/acum6``) + ``water_cut``,
      ``produjo_mes_pasado`` y ``prod_vecinos_mean`` (genéricas).
    - **Nuevas recursion-safe:** más autorregresivas del propio target
      (``{target}_lag2/lag3/roll6/acum12/delta3/ratio1/std3/cummax/frac_peak/``
      ``meses_desde_pico``) + ``well_age_months`` (genérica). Se pueden recalcular
      en un mes futuro desde la trayectoria del target → habilitan el forecast
      recursivo. ``prod_vecinos_mean`` (cross-well) y ``water_cut`` (usa agua) NO
      lo son; se dejan en la lista pero se filtran para el modelo recursivo.

    Las features con prefijo del target no colisionan entre petróleo y gas; las
    genéricas comparten nombre (cada modelo tiene su propio dataset).
    """
    return [
        f"{target}_roll3",
        f"{target}_delta1",
        f"{target}_lag12",
        f"{target}_acum6",
        "water_cut",
        "produjo_mes_pasado",
        "prod_vecinos_mean",
        # --- Nuevas features RECURSION-SAFE (autorregresivas del propio target +
        # edad del pozo): se pueden recalcular en un mes futuro a partir de la
        # trayectoria del target, así habilitan el forecast recursivo. Ver ADR
        # de rediseño de features. `well_age_months` es genérica (no depende del
        # target); el resto lleva el prefijo del target.
        f"{target}_lag2",
        f"{target}_lag3",
        f"{target}_roll6",
        f"{target}_acum12",
        f"{target}_delta3",
        f"{target}_ratio1",
        f"{target}_std3",
        f"{target}_cummax",
        f"{target}_frac_peak",
        f"{target}_meses_desde_pico",
        "well_age_months",
    ]


# Lista por defecto (target petróleo), para compatibilidad con los imports
# existentes (``features.ENGINEERED_FEATURES``, p. ej. en feature_store_build).
ENGINEERED_FEATURES = engineered_feature_names("prod_pet")


# --- Set de features del modelo (fuente única de verdad) --------------------

# Features NO recursion-safe (ADR-043/044): no se pueden recalcular en un mes futuro
# desde la trayectoria del propio target. Genéricas (mismo nombre para los dos targets);
# la producción CRUZADA (el otro target) la resuelve `recursion_safe_cols`.
NON_RECURSION_SAFE = ["prod_vecinos_mean", "water_cut", "prod_agua", "tef"]


def recursion_safe_cols(feature_cols: list[str], target: str = "prod_pet") -> list[str]:
    """Filtra las features NO recursion-safe para el forecast recursivo (ADR-043/044).

    Quita ``prod_vecinos_mean`` (cross-well), ``water_cut``/``prod_agua``/``tef`` (series
    medidas que no se forecastean) y la producción **cruzada** (el otro target). El resto
    —autorregresivas del propio target + estáticas + calendario— se puede recalcular en
    cada paso recursivo. Es el **set candidato** que rankean los notebooks 02/03; el set
    **final** del modelo es `selected_features`.
    """
    cross = "prod_gas" if target == "prod_pet" else "prod_pet"
    excluir = set(NON_RECURSION_SAFE) | {cross}
    return [c for c in feature_cols if c not in excluir]


def selected_features(target: str = "prod_pet") -> list[str]:
    """Set de features del modelo (ADR-043): las de **ganancia positiva** de la
    selección por *permutation importance* en val — aquellas cuya permutación
    **empeora** el RMSE de val (``imp_mean > 0``, ``ml.selection.select_features``).
    Es lo que entrena `ml/train.py`, lo que materializa el feature store (ADR-036) y
    lo que consume el forecast recursivo (ADR-044) — única fuente de verdad de las
    tres puntas, así no pueden divergir.

    Los sets de petróleo y gas **difieren** (cada target rankeó distinto): **27**
    features para ``prod_pet`` y **19** para ``prod_gas`` (notebooks 02/03). Ambos
    salen de rankear el candidato recursion-safe de 35, así que **todas** son
    recursion-safe: las autorregresivas se recalculan desde la trayectoria del target
    en el forecast y las estáticas (reservorio, física, ubicación, categóricas del
    pozo) se replican en cada paso. Las de importancia ≈ 0 o negativa quedan **fuera**
    (ruido). El orden replica el ranking (importancia desc).
    """
    if target == "prod_pet":
        return [
            # Núcleo autorregresivo del target (ganancia positiva en val)
            "prod_pet", "prod_pet_roll3", "prod_pet_ratio1", "prod_pet_acum12",
            "prod_pet_roll6", "prod_pet_acum6", "prod_pet_cummax", "prod_pet_lag2",
            "prod_pet_delta1", "prod_pet_std3", "prod_pet_frac_peak",
            "prod_pet_lag12", "prod_pet_lag3", "prod_pet_meses_desde_pico",
            "produjo_mes_pasado",
            # Anclas estáticas / categóricas del pozo (cold-start)
            "areayacimiento", "profundidad", "coordenadax", "coordenaday",
            "well_age_months", "areapermisoconcesion", "tipopozo", "empresa",
            "mes", "tipoextraccion", "proyecto", "cuenca",
        ]
    if target == "prod_gas":
        return [
            "prod_gas", "prod_gas_roll3", "prod_gas_roll6", "prod_gas_acum12",
            "prod_gas_acum6", "prod_gas_ratio1", "prod_gas_lag3", "prod_gas_lag12",
            "prod_gas_std3", "prod_gas_cummax", "prod_gas_delta3", "prod_gas_lag2",
            # Anclas estáticas / categóricas del pozo (cold-start)
            "tipoestado", "mes", "clasificacion", "sub_tipo_recurso",
            "provincia", "formprod", "tipoextraccion",
        ]
    raise ValueError(f"target no soportado: {target!r} (esperado 'prod_pet' o 'prod_gas')")


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


# --- Nuevas features recursion-safe (autorregresivas del propio target) -----
# Todas usan solo meses <= t (lags por calendario o acumulados/expanding sobre la
# historia observada del pozo), así que respetan el anti-leakage y, en el forecast
# recursivo, se pueden actualizar con la propia predicción del mes siguiente.

def add_prod_pet_lag2(df: pd.DataFrame, col: str = "prod_pet") -> pd.DataFrame:
    """Nivel del target 2 meses antes (``{col}_lag2``)."""
    df[f"{col}_lag2"] = _calendar_lag(df, col, 2)
    return df


def add_prod_pet_lag3(df: pd.DataFrame, col: str = "prod_pet") -> pd.DataFrame:
    """Nivel del target 3 meses antes (``{col}_lag3``)."""
    df[f"{col}_lag3"] = _calendar_lag(df, col, 3)
    return df


def add_prod_pet_roll6(df: pd.DataFrame, col: str = "prod_pet") -> pd.DataFrame:
    """Media móvil de {t..t-5}: nivel reciente a 6 meses (``{col}_roll6``)."""
    lags = pd.DataFrame({k: _calendar_lag(df, col, k) for k in range(6)})
    df[f"{col}_roll6"] = lags.mean(axis=1)  # skipna: usa los meses disponibles
    return df


def add_prod_pet_acum12(df: pd.DataFrame, col: str = "prod_pet") -> pd.DataFrame:
    """Producción acumulada en los últimos 12 meses (``{col}_acum12``)."""
    lags = pd.DataFrame({k: _calendar_lag(df, col, k) for k in range(12)})
    df[f"{col}_acum12"] = lags.sum(axis=1, min_count=1)  # NaN solo si no hay ningún mes
    return df


def add_prod_pet_delta3(df: pd.DataFrame, col: str = "prod_pet") -> pd.DataFrame:
    """Variación absoluta a 3 meses ``col(t) - col(t-3)`` (``{col}_delta3``):
    declinación de mediano plazo (un trimestre)."""
    df[f"{col}_delta3"] = df[col].to_numpy() - _calendar_lag(df, col, 3)
    return df


def add_prod_pet_ratio1(df: pd.DataFrame, col: str = "prod_pet") -> pd.DataFrame:
    """Declinación **multiplicativa** mes a mes ``col(t) / col(t-1)`` (``{col}_ratio1``).

    Las curvas de producción declinan casi exponencialmente, así que el cociente
    suele capturar mejor la tasa que la diferencia. NaN si no hay mes t-1 o si
    ``col(t-1) == 0`` (cociente indefinido)."""
    prev = _calendar_lag(df, col, 1)
    cur = df[col].to_numpy(dtype=float)
    out = np.full(len(df), np.nan)
    np.divide(cur, prev, out=out, where=prev > 0)  # NaN donde prev es NaN o 0
    df[f"{col}_ratio1"] = out
    return df


def add_prod_pet_std3(df: pd.DataFrame, col: str = "prod_pet") -> pd.DataFrame:
    """Desvío estándar de {t, t-1, t-2}: volatilidad reciente (``{col}_std3``).
    NaN si hay menos de 2 meses disponibles."""
    lags = pd.DataFrame({k: _calendar_lag(df, col, k) for k in (0, 1, 2)})
    df[f"{col}_std3"] = lags.std(axis=1)  # ddof=1, skipna
    return df


def add_prod_pet_cummax(df: pd.DataFrame, col: str = "prod_pet") -> pd.DataFrame:
    """Pico histórico del target hasta t inclusive (``{col}_cummax``).

    ``expanding max`` por pozo sobre la historia observada: como ``df`` viene
    ordenado por ``(idpozo, periodo)``, en cada fila usa solo meses <= t (los huecos
    de calendario simplemente no se cuentan). Recursion-safe: en el futuro se
    actualiza con ``max(cummax, predicción)``."""
    df[f"{col}_cummax"] = df.groupby("idpozo")[col].cummax()
    return df


def add_prod_pet_frac_peak(df: pd.DataFrame, col: str = "prod_pet") -> pd.DataFrame:
    """Fracción del pico ``col(t) / cummax(t)`` en [0, 1] (``{col}_frac_peak``):
    en qué etapa de la declinación está el pozo (1 = en su máximo histórico).

    Requiere ``{col}_cummax`` ya calculado. Si el pozo nunca produjo (cummax 0) se
    define 0."""
    cummax = df[f"{col}_cummax"].to_numpy(dtype=float)
    cur = df[col].to_numpy(dtype=float)
    out = np.zeros(len(df))
    np.divide(cur, cummax, out=out, where=cummax > 0)
    df[f"{col}_frac_peak"] = out
    return df


def add_meses_desde_pico(df: pd.DataFrame, col: str = "prod_pet") -> pd.DataFrame:
    """Meses transcurridos desde el mes del pico histórico (hasta t)
    (``{col}_meses_desde_pico``): 0 en el mes del pico, crece durante la declinación.

    Requiere ``{col}_cummax``. El mes del pico es aquel en que ``col`` alcanzó el
    máximo acumulado; se propaga hacia adelante (``ffill``) por pozo. Recursion-safe:
    si una predicción futura supera el pico, se reinicia a 0."""
    cummax = df[f"{col}_cummax"].to_numpy(dtype=float)
    es_pico = df[col].to_numpy(dtype=float) >= cummax  # este mes fijó (o igualó) el máx
    periodo_pico = df["periodo"].where(pd.Series(es_pico, index=df.index))
    periodo_pico = periodo_pico.groupby(df["idpozo"]).ffill()
    meses = (
        (df["periodo"].dt.year - periodo_pico.dt.year) * 12
        + (df["periodo"].dt.month - periodo_pico.dt.month)
    )
    df[f"{col}_meses_desde_pico"] = meses.to_numpy()
    return df


def add_well_age_months(df: pd.DataFrame) -> pd.DataFrame:
    """Edad del pozo en meses = meses desde su primer mes observado
    (``well_age_months``, genérica). Madurez pura de la propia serie; el mínimo por
    pozo siempre es <= t, así que no hay leakage."""
    primero = df.groupby("idpozo")["periodo"].transform("min")
    df["well_age_months"] = (
        (df["periodo"].dt.year - primero.dt.year) * 12
        + (df["periodo"].dt.month - primero.dt.month)
    ).to_numpy()
    return df


def add_engineered_features(
    df: pd.DataFrame, target: str = "prod_pet", k_vecinos: int = 5
) -> pd.DataFrame:
    """Agrega las features de ingeniería sobre el panel mensual ``(pozo, mes)``,
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
    # históricas
    df = add_prod_pet_roll3(df, col=target)
    df = add_prod_pet_delta1(df, col=target)
    df = add_prod_pet_lag12(df, col=target)
    df = add_prod_pet_acum6(df, col=target)
    df = add_water_cut(df)
    df = add_produjo_mes_pasado(df, col=target)
    # nuevas recursion-safe (autorregresivas del target + edad del pozo)
    df = add_prod_pet_lag2(df, col=target)
    df = add_prod_pet_lag3(df, col=target)
    df = add_prod_pet_roll6(df, col=target)
    df = add_prod_pet_acum12(df, col=target)
    df = add_prod_pet_delta3(df, col=target)
    df = add_prod_pet_ratio1(df, col=target)
    df = add_prod_pet_std3(df, col=target)
    df = add_prod_pet_cummax(df, col=target)          # antes de frac_peak / meses_desde_pico
    df = add_prod_pet_frac_peak(df, col=target)
    df = add_meses_desde_pico(df, col=target)
    df = add_well_age_months(df)
    # cross-well: va al final porque su merge castea idpozo a object y reordena
    df = add_prod_vecinos_mean(df, col=target, k=k_vecinos)
    return df
