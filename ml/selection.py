"""Feature selection leakage-safe para el forecast t+1 (Fase 3).

Se selecciona **sobre el modelo ya tuneado** (ADR-028: primero hiperparámetros,
después features): se rankean las features por *permutation importance* medida en
**val** y se evalúan subconjuntos top-k para quedarse con el set más chico que no
pierde RMSE.

**Granularidad = feature cruda, no las dummies del one-hot.** Como el ``Pipeline``
recibe ``X`` sin codificar (el one-hot vive adentro), permutar una columna cruda
(p. ej. ``empresa`` entera) mide el aporte de **esa feature completa**, no de una
categoría suelta. Eso evita el problema clásico de rankear ~380 dummies sueltas.

**Anti-leakage:** el modelo se ajusta en train y la importancia se mide en **val**
(el mismo set con el que ya se comparan los modelos tuneados, notebook 03 §4.1);
``test`` queda intacto. Medir la importancia en val y no en train evita premiar
features que el modelo *memorizó* pero que no generalizan. La selección es un paso
más de *model selection*, no la evaluación final.

**Lectura de la importancia:** con el scorer de RMSE, la importancia queda en las
mismas unidades del target (m³) = **cuánto sube el RMSE de val al romper esa
feature**. > 0 = útil; ≈ 0 o < 0 = ruido (permutarla no empeora, o hasta mejora).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.inspection import permutation_importance

from ml import modeling
from ml.config import RANDOM_STATE


def permutation_ranking(
    estimator, X_val: pd.DataFrame, y_val, n_repeats: int = 10,
    random_state: int = RANDOM_STATE, n_jobs: int = 1,
) -> pd.DataFrame:
    """Rankea las features por *permutation importance* en **val**.

    ``estimator`` es el ``Pipeline`` campeón **ya ajustado en train** (el
    ``best_estimator_`` de la búsqueda). Para cada feature se permuta su columna en
    ``X_val`` (``n_repeats`` veces) y se mide cuánto empeora el RMSE de val; el
    ``Pipeline`` hace el one-hot/imputación internamente, así que se permuta a nivel
    de **feature cruda**.

    Devuelve un DataFrame ordenado por importancia (desc) con ``feature``,
    ``imp_mean`` (m³ de aumento de RMSE al romper la feature) e ``imp_std``.
    """
    r = permutation_importance(
        estimator, X_val, y_val,
        scoring=modeling.RMSE_SCORER, n_repeats=n_repeats,
        random_state=random_state, n_jobs=n_jobs,
    )
    return (
        pd.DataFrame({
            "feature": list(X_val.columns),
            "imp_mean": r.importances_mean,
            "imp_std": r.importances_std,
        })
        .sort_values("imp_mean", ascending=False)
        .reset_index(drop=True)
    )


def _model_and_scale(champion_pipe) -> tuple[object, bool]:
    """Extrae ``(modelo_sin_ajustar, usa_escalado)`` del ``Pipeline`` campeón, para
    reentrenar con los mismos hiperparámetros sobre otro conjunto de features."""
    model = clone(champion_pipe.named_steps["model"])
    scale = "scaler" in champion_pipe.named_steps
    return model, scale


def evaluate_topk(
    ranking: pd.DataFrame, champion_pipe,
    X_train: pd.DataFrame, y_train, X_val: pd.DataFrame, y_val,
    ks: list[int] | None = None,
) -> pd.DataFrame:
    """RMSE/R² en val al **reentrenar el campeón** usando solo las top-k features
    del ``ranking`` (mismos hiperparámetros, distinto subconjunto de columnas).

    Sirve para elegir el set más chico que no pierde performance: se busca la
    "rodilla" de la curva (a partir de cierto k, sumar features ya no baja el RMSE).
    Si ``ks`` es ``None`` se usa una grilla razonable acotada al nº de features.
    """
    model, scale = _model_and_scale(champion_pipe)
    order = ranking["feature"].tolist()
    if ks is None:
        ks = [k for k in (3, 5, 8, 10, 15, 20, len(order)) if k <= len(order)]
        ks = sorted(set(ks))

    filas = []
    for k in ks:
        cols = order[:k]
        pipe = modeling.build_pipeline(clone(model), scale, cols)
        pipe.fit(X_train[cols], y_train)
        filas.append({"k": k, **modeling.evaluate(y_val, pipe.predict(X_val[cols]))})
    return pd.DataFrame(filas)


def select_features(ranking: pd.DataFrame, min_importance: float = 0.0) -> list[str]:
    """Features con importancia por encima de un umbral (default: > 0, es decir,
    las que al romperse **empeoran** el RMSE de val).

    Para ser más estricto (dejar solo las que superan su propio ruido) usar
    ``min_importance`` mayor, o filtrar por ``imp_mean > imp_std`` sobre el ranking.
    """
    return ranking.loc[ranking["imp_mean"] > min_importance, "feature"].tolist()


def refit_and_eval(
    champion_pipe, cols: list[str],
    X_train: pd.DataFrame, y_train, X_val: pd.DataFrame, y_val,
) -> tuple[object, dict[str, float]]:
    """Reentrena el campeón con las features ``cols`` y lo evalúa en val.

    Devuelve ``(pipeline_ajustado, métricas_val)``. Se usa para comparar el modelo
    **completo** vs el **reducido** con exactamente los mismos hiperparámetros.
    """
    model, scale = _model_and_scale(champion_pipe)
    pipe = modeling.build_pipeline(clone(model), scale, cols)
    pipe.fit(X_train[cols], y_train)
    return pipe, modeling.evaluate(y_val, pipe.predict(X_val[cols]))
