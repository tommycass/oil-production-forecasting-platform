"""Motor de **forecast recursivo multi-paso** para el pronóstico mensual (ADR-042).

El modelo predice **un mes hacia adelante** (t+1). Para cubrir un rango se encadenan
predicciones: se predice t+1, se trata esa predicción como si fuera dato observado, se
**recalculan las features recursion-safe** (ADR-041) sobre la serie extendida y se
predice t+2, y así sucesivamente (*recursivo / multi-paso*).

Esto es posible **solo** porque el modelo se entrena con features recursion-safe
(ADR-041): las que quedaron (autorregresivas del propio target + estáticas + calendario)
se pueden **recalcular en cada paso** a partir de la trayectoria del target. Las que se
excluyeron (`prod_vecinos_mean`, `water_cut`, la producción cruzada, `prod_agua`, `tef`)
no se pueden proyectar hacia el futuro; por eso no entran al modelo y el motor no las
necesita.

Este módulo es **puro y testeable**: recibe el `pipeline` entrenado, la **serie mensual
observada** del pozo y sus **atributos estáticos**, y devuelve la serie pronosticada. No
toca la base de datos ni la API; leer la historia del pozo del feature store/DW es
responsabilidad del serving (``api/app/services``).

**Estrategia de features (feature store en inferencia, ADR-035/042):**
- **Primer mes (t+1):** se usan las **features pre-computadas del feature store** (la fila del
  mes base ``base_features``), **sin recalcular** — el store se usa en inferencia (RNF Fase 3).
- **Meses futuros (t+2+):** se **recalculan** las features recursion-safe desde la serie
  extendida, porque esos meses **no existen** en el store (dependen de predicciones). Es
  inevitable en un forecast recursivo.
- Las **estáticas** (``profundidad``, coords, categóricas) nunca se recalculan: vienen del
  store y se replican.

El recálculo de los pasos futuros es **paridad-safe** por construcción: usa **las mismas
funciones de ``ml/features.py``** con las que el store materializa, sobre la serie del pozo →
sin training-serving skew.
"""
from __future__ import annotations

import pandas as pd

from ml import features as F

# Features de ingeniería **recursion-safe** que el motor recalcula en cada paso: son
# exactamente las de ``features.add_engineered_features`` MENOS ``water_cut`` (necesita
# ``prod_agua`` del futuro) y ``prod_vecinos_mean`` (cross-well, necesita otros pozos).
# Todas se derivan de la trayectoria del propio target (+ edad del pozo), así que se
# actualizan con la propia predicción del mes siguiente. El orden replica el de training.
def _add_recursion_safe_features(df: pd.DataFrame, target: str) -> pd.DataFrame:
    """Agrega, sobre el panel de **un** pozo, las features autorregresivas recursion-safe
    del ``target`` (más ``well_age_months``). Reusa las mismas funciones que el training
    (``ml/features.py``) para garantizar **paridad training-serving** (ADR-035)."""
    df = df.sort_values(["idpozo", "periodo"]).reset_index(drop=True)
    df = F.add_prod_pet_roll3(df, col=target)
    df = F.add_prod_pet_delta1(df, col=target)
    df = F.add_prod_pet_lag12(df, col=target)
    df = F.add_prod_pet_acum6(df, col=target)
    df = F.add_produjo_mes_pasado(df, col=target)
    df = F.add_prod_pet_lag2(df, col=target)
    df = F.add_prod_pet_lag3(df, col=target)
    df = F.add_prod_pet_roll6(df, col=target)
    df = F.add_prod_pet_acum12(df, col=target)
    df = F.add_prod_pet_delta3(df, col=target)
    df = F.add_prod_pet_ratio1(df, col=target)
    df = F.add_prod_pet_std3(df, col=target)
    df = F.add_prod_pet_cummax(df, col=target)  # antes de frac_peak / meses_desde_pico
    df = F.add_prod_pet_frac_peak(df, col=target)
    df = F.add_meses_desde_pico(df, col=target)
    df = F.add_well_age_months(df)
    return df


def _seed_panel(history: pd.DataFrame, static: dict, target: str) -> pd.DataFrame:
    """Arma el panel inicial ``(idpozo, periodo, target)`` + atributos estáticos a partir
    de la serie observada. Los estáticos (profundidad, coords, categóricas) se replican en
    cada fila; son constantes en el tiempo (no cambian mes a mes)."""
    panel = history[["periodo", target]].copy()
    panel["periodo"] = pd.to_datetime(panel["periodo"])
    panel["idpozo"] = static.get("idpozo", 0)
    for col, val in static.items():
        if col != "idpozo":
            panel[col] = val
    return panel.sort_values("periodo").reset_index(drop=True)


def _model_columns(pipeline) -> list[str] | None:
    """Columnas (crudas) con las que se entrenó el ``pipeline``, para pasarle exactamente
    esas al predecir. sklearn las expone en ``feature_names_in_`` cuando el fit fue con un
    DataFrame; si no están, devolvemos ``None`` y se le pasa la fila completa (el
    ColumnTransformer selecciona por nombre e ignora columnas de más)."""
    # Ojo: feature_names_in_ es un ndarray → no usar `or` (ambiguo en arrays); chequear None.
    names = getattr(pipeline, "feature_names_in_", None)
    return list(names) if names is not None else None


def recursive_forecast(
    pipeline,
    base_features: dict,
    series: pd.DataFrame,
    static: dict,
    target: str,
    n_steps: int,
    floor: float = 0.0,
) -> list[dict]:
    """Pronostica ``n_steps`` meses hacia adelante de forma **recursiva**.

    **Primer paso (t+1): features del feature store, sin recalcular** (RNF de Fase 3,
    ADR-035). El mes base ``t`` = último mes observado ya está materializado en el store con
    todas sus features; ``base_features`` es esa fila y se usa **directamente** para predecir
    t+1 (es lo mismo que hacía ``/predict``). **Pasos futuros (t+2, t+3, …): se recalculan**
    las features recursion-safe desde la serie extendida, porque esos meses **no existen** en
    el store (dependen de predicciones). El recálculo usa las mismas funciones de
    ``ml/features.py`` que materializan el store → sin training-serving skew.

    Args:
        pipeline: modelo entrenado (``Pipeline`` sklearn, recursion-safe).
        base_features: fila de features del **mes base** ``t`` leída del feature store (dict
            ``{columna: valor}``). Se usa para predecir el primer mes (t+1) sin recalcular.
        series: serie mensual **observada** del pozo (``periodo`` + ``target``), para
            **recalcular** las features de los meses futuros (t+2+). Una fila por mes.
        static: atributos **estáticos** del pozo (``profundidad``, coords, categóricas). Se
            replican en cada mes recalculado. Puede incluir ``idpozo``.
        target: ``prod_pet`` o ``prod_gas``.
        n_steps: cantidad de meses futuros a predecir (>= 1). El primero es
            ``último_observado + 1``.
        floor: piso físico de la producción (0 por defecto). Cada predicción se recorta a
            ``max(pred, floor)`` antes de realimentarla.

    Returns:
        Lista de ``{"periodo": Timestamp, target: float}`` de largo ``n_steps``, un punto por
        mes, empezando en ``último_observado + 1``. Lista vacía si ``n_steps <= 0``.

    Raises:
        ValueError: si ``series`` está vacía (sin trayectoria para sembrar/recalcular).
    """
    if series is None or len(series) == 0:
        raise ValueError("series vacía: no hay trayectoria observada para el forecast")
    if n_steps <= 0:
        return []

    cols = _model_columns(pipeline)
    ultimo = pd.to_datetime(max(series["periodo"]))  # último mes observado (L)
    panel = _seed_panel(series, static, target)       # para recalcular los pasos futuros
    out: list[dict] = []

    for i in range(n_steps):
        proximo = ultimo + pd.DateOffset(months=i + 1)
        if i == 0:
            # paso 1: features PRE-COMPUTADAS del store (mes base) → sin recalcular (RNF)
            fila = pd.DataFrame([base_features])
        else:
            # pasos futuros: recalcular las recursion-safe desde la serie extendida
            feat = _add_recursion_safe_features(panel.copy(), target)
            fila = feat.iloc[[-1]].copy()
        fila["mes"] = proximo.month  # mes OBJETIVO (t+1 de este paso), conocido de antemano
        X = fila.reindex(columns=cols) if cols else fila
        pred = max(float(pipeline.predict(X)[0]), floor)
        out.append({"periodo": proximo, target: pred})

        # realimentar: apendar el mes predicho a la serie/panel para el próximo recompute
        nueva = {c: panel.iloc[-1][c] for c in panel.columns}  # arrastra estáticos
        nueva["periodo"] = proximo
        nueva[target] = pred
        panel = pd.concat([panel, pd.DataFrame([nueva])], ignore_index=True)

    return out
