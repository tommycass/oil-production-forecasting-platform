"""Entrenamiento y comparación de modelos para el forecast de ``prod_pet`` (t+1).

Funciones reutilizables para:
- armar la matriz de features ``X`` (crudas: numéricas + categóricas) y el target ``y``,
- **entrenar y evaluar** (RMSE, R²) modelos comparables sobre val,
- **tunear hiperparámetros con CV temporal** (expanding window por mes).

Todo el preprocesamiento (**one-hot, imputación de NaN y normalización**) vive
**dentro de un ``Pipeline``** (``build_pipeline``): así se **ajusta solo con el
train de cada fold** durante la cross-validation y solo con train en la evaluación
final → sin leakage val/test→train en ningún estadístico aprendido.

Se entrena en ``train`` y se compara en ``val`` (ADR-028); ``test`` queda intacto.
Métricas: RMSE (m³) y R² (varianza explicada).
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import make_scorer, mean_squared_error, r2_score
from sklearn.model_selection import ParameterGrid, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from tqdm.auto import tqdm
from xgboost import XGBRegressor

from ml import dataset, preprocessing

# columnas que NO son features (claves, target)
KEYS = ["idpozo", "periodo", "periodo_objetivo", "split"]
TARGET_COL = "y_next"

# modelos que requieren normalización (escala) de las features
NEEDS_SCALING = {"reg_lineal"}


# --- Matriz de features y split --------------------------------------------

def build_feature_matrix(ds: pd.DataFrame | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Devuelve ``(ds, feature_cols)``: el dataset con las features **crudas**
    (numéricas + categóricas sin codificar) y la lista de columnas que son features.

    El one-hot NO se hace acá: vive en el ``Pipeline`` (``build_pipeline``) para
    poder ajustarlo por fold durante la CV. Si no se pasa ``ds``, se construye con
    ``ml.dataset.build_basic_dataset``.
    """
    if ds is None:
        ds = dataset.build_basic_dataset()
    feature_cols = [c for c in ds.columns if c not in KEYS + [TARGET_COL]]
    return ds, feature_cols


def split_train_val(
    ds: pd.DataFrame, feature_cols: list[str]
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
    """Separa en (X_train, y_train, X_val, y_val) según la columna ``split``.

    Devuelve ``X`` como DataFrame con las columnas crudas (el ``Pipeline`` se
    encarga del one-hot/imputación/escalado, ajustando en train)."""
    usable = ds[ds[TARGET_COL].notna()]
    tr = usable[usable.split == "train"]
    va = usable[usable.split == "val"]
    return (
        tr[feature_cols], tr[TARGET_COL],
        va[feature_cols], va[TARGET_COL],
    )


def split_dev_test(
    ds: pd.DataFrame, feature_cols: list[str]
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
    """Separa en (X_dev, y_dev, X_test, y_test): **dev = train + val** para el
    entrenamiento final del modelo elegido, y **test** para la evaluación única.

    El preprocesamiento se ajusta sobre dev (test queda fuera), igual que en la CV
    se ajustaba por fold: ningún estadístico ve test (anti-leakage)."""
    usable = ds[ds[TARGET_COL].notna()]
    dev = usable[usable.split.isin(["train", "val"])]
    te = usable[usable.split == "test"]
    return (
        dev[feature_cols], dev[TARGET_COL],
        te[feature_cols], te[TARGET_COL],
    )


# --- Preprocesamiento + modelo, todo dentro del Pipeline -------------------

def build_pipeline(estimator, scale: bool, feature_cols: list[str]) -> Pipeline:
    """Pipeline de preprocesamiento por feature + modelo, con **todo lo aprendido
    fit en train**.

    El preprocesamiento (``ml.preprocessing.build_preprocessor``: imputación de NaN
    por feature + flags, one-hot con ``DESCONOCIDO``) y el
    ``StandardScaler`` opcional viven dentro del ``Pipeline``. Por eso, en la CV,
    **todos los estadísticos (medianas, percentiles, vocabulario, media/desvío) se
    reajustan solo con el train de cada fold** (anti-leakage val→train); y el train
    son filas ``periodo <= TRAIN_END`` (pasado). El escalado solo se agrega para los
    modelos lineales (``scale=True``); los árboles no lo necesitan.
    """
    steps = [("prep", preprocessing.build_preprocessor(feature_cols))]
    if scale:
        steps.append(("scaler", StandardScaler()))
    steps.append(("model", estimator))
    return Pipeline(steps)


# --- Modelos y evaluación --------------------------------------------------

def get_models(random_state: int = 42) -> dict:
    """Modelos a comparar, con hiperparámetros razonables para un primer barrido.

    El comparador lineal es ``Ridge`` (no ``LinearRegression`` pelada): con ~380
    dummies colineales del one-hot, la regresión sin regularizar es numéricamente
    inestable; una L2 chica la estabiliza. Es la misma familia que se tunea.
    """
    return {
        "reg_lineal": Ridge(alpha=1.0, random_state=random_state),
        "random_forest": RandomForestRegressor(
            n_estimators=100, max_features="sqrt", min_samples_leaf=5,
            n_jobs=-1, random_state=random_state,
        ),
        "xgboost": XGBRegressor(
            n_estimators=400, learning_rate=0.05, max_depth=6,
            subsample=0.8, colsample_bytree=0.8, tree_method="hist",
            n_jobs=-1, random_state=random_state,
        ),
    }


# Mejores hiperparámetros registrados (del tuning con CV temporal, notebook
# 03_modeling §4.1 / ADR-037). Si no se tunea, se usan estos en vez de defaults
# arbitrarios. (Idealmente vendrían del Model Registry de MLflow — Rol 3; por
# ahora se mantienen acá como "últimos mejores registrados".)
BEST_PARAMS = {
    "ridge": {"alpha": 1000.0},
    "random_forest": {
        "n_estimators": 300, "max_depth": None,
        "max_features": 0.3, "min_samples_leaf": 5,
    },
    "xgboost": {
        "n_estimators": 400, "learning_rate": 0.05, "max_depth": 4,
        "subsample": 0.8, "colsample_bytree": 0.8,
    },
}


def make_estimator(name: str, params: dict | None = None, random_state: int = 42):
    """Construye el estimador ``name`` con ``params`` (o ``BEST_PARAMS[name]`` si
    no se pasan): los **últimos mejores hiperparámetros registrados** (ADR-037)."""
    params = BEST_PARAMS[name] if params is None else params
    if name == "ridge":
        return Ridge(**params)
    if name == "random_forest":
        return RandomForestRegressor(n_jobs=-1, random_state=random_state, **params)
    if name == "xgboost":
        return XGBRegressor(tree_method="hist", n_jobs=-1, random_state=random_state, **params)
    raise ValueError(f"modelo desconocido: {name}")


def rmse(y_true, y_pred) -> float:
    """RMSE compatible con todas las versiones de sklearn (no usa
    ``root_mean_squared_error``, que recién existe desde sklearn 1.4)."""
    return float(np.sqrt(mean_squared_error(y_true, y_pred)))


# scorer de RMSE para cross_val_score (negativo: sklearn maximiza el score)
RMSE_SCORER = make_scorer(rmse, greater_is_better=False)


def evaluate(y_true, y_pred) -> dict[str, float]:
    """RMSE (m³, penaliza errores grandes) y R² (varianza explicada)."""
    return {
        "val_rmse": rmse(y_true, y_pred),
        "val_r2": r2_score(y_true, y_pred),
    }


def persistence_val(ds: pd.DataFrame) -> dict[str, float]:
    """Baseline de persistencia en val: ŷ(t+1) = prod_pet(t). Referencia a batir
    (ADR-029); ``prod_pet`` en el dataset es justo la producción del mes t."""
    va = ds[(ds.split == "val") & ds[TARGET_COL].notna()]
    return evaluate(va[TARGET_COL], va["prod_pet"])


def train_eval_models(
    X_train: pd.DataFrame, y_train: pd.Series,
    X_val: pd.DataFrame, y_val: pd.Series,
    models: dict | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Entrena cada modelo (dentro de su ``Pipeline``) en train y lo evalúa en val.

    Cada modelo se envuelve en ``build_pipeline`` (one-hot + imputación [+ escalado])
    ajustado **solo en train**. Devuelve ``(tabla ordenada por val_rmse, pipelines)``.
    """
    models = models or get_models()
    feature_cols = list(X_train.columns)

    filas, entrenados = [], {}
    for nombre, modelo in models.items():
        pipe = build_pipeline(clone(modelo), nombre in NEEDS_SCALING, feature_cols)
        pipe.fit(X_train, y_train)
        filas.append({"modelo": nombre, **evaluate(y_val, pipe.predict(X_val))})
        entrenados[nombre] = pipe

    tabla = pd.DataFrame(filas).sort_values("val_rmse").reset_index(drop=True)
    return tabla, entrenados


# --- Búsqueda de hiperparámetros con CV temporal ---------------------------

def time_series_folds(periodos, n_splits: int = 4) -> list[tuple[np.ndarray, np.ndarray]]:
    """Folds de CV que respetan el orden temporal (expanding window), a nivel de
    **mes**, para un panel (varios pozos por mes).

    Estilo ``TimeSeriesSplit`` pero sobre los meses únicos: en cada fold el bloque
    de validación es **posterior** a todos los meses de entrenamiento, así nunca
    se entrena con datos del futuro respecto de lo que se valida (anti-leakage
    futuro→pasado). Como el preprocesamiento (one-hot, imputación, escalado) va
    dentro del ``Pipeline``, ``cross_val_score`` lo reajusta **solo con el tramo de
    train de cada fold** → tampoco hay leakage de estadísticos entre folds.

    Devuelve una lista de ``(train_idx, val_idx)`` con índices **posicionales**
    (alineados con el orden de las filas de ``X``).
    """
    periodos = np.asarray(periodos)
    meses = np.sort(np.unique(periodos))
    bloques = np.array_split(meses, n_splits + 1)
    folds = []
    train_meses = bloques[0]
    for i in range(1, n_splits + 1):
        val_meses = bloques[i]
        tr_idx = np.where(np.isin(periodos, train_meses))[0]
        va_idx = np.where(np.isin(periodos, val_meses))[0]
        folds.append((tr_idx, va_idx))
        train_meses = np.concatenate([train_meses, val_meses])  # ventana creciente
    return folds


def train_search_arrays(
    ds: pd.DataFrame, feature_cols: list[str]
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """Datos de train (ordenados por periodo) para la búsqueda: ``(X, y, periodos)``.

    ``X`` es un DataFrame con las columnas **crudas** (el one-hot lo hace el
    ``Pipeline`` por fold); ``y`` y ``periodos`` van como numpy. El orden de filas
    es el mismo en los tres, para que los índices posicionales de los folds
    coincidan exactamente con las filas de ``X``.
    """
    tr = (
        ds[(ds.split == "train") & ds[TARGET_COL].notna()]
        .sort_values("periodo")
        .reset_index(drop=True)
    )
    return tr[feature_cols], tr[TARGET_COL].to_numpy(), tr["periodo"].to_numpy()


def search_spaces(random_state: int = 42) -> dict:
    """Espacios de búsqueda por modelo. ``ridge`` tunea la lambda L2 (``alpha``);
    los árboles, sus hiperparámetros principales. Grids chicos a propósito
    (ampliables) para que la corrida sea manejable.

    ``search_n_jobs``: para los árboles (que ya paralelizan internamente con
    ``n_jobs=-1``) se deja en 1 para no sobre-suscribir CPU; para ridge, -1.
    """
    return {
        "ridge": {
            "estimator": Ridge(),
            "scale": True,
            "search_n_jobs": -1,
            "grid": {"model__alpha": [0.01, 0.1, 1.0, 10.0, 100.0, 1000.0]},
        },
        "random_forest": {
            "estimator": RandomForestRegressor(n_jobs=-1, random_state=random_state),
            "scale": False,
            "search_n_jobs": 1,
            "grid": {
                "model__n_estimators": [300],
                "model__max_depth": [None, 16],
                "model__min_samples_leaf": [5, 20],
                "model__max_features": ["sqrt", 0.3],
            },
        },
        "xgboost": {
            "estimator": XGBRegressor(
                tree_method="hist", n_jobs=-1, random_state=random_state
            ),
            "scale": False,
            "search_n_jobs": 1,
            "grid": {
                "model__n_estimators": [400, 800],
                "model__learning_rate": [0.03, 0.05],
                "model__max_depth": [4, 6, 8],
                "model__subsample": [0.8],
                "model__colsample_bytree": [0.8],
            },
        },
    }


def tune_model(
    spec: dict, X_train: pd.DataFrame, y_train: np.ndarray,
    periodos_train: np.ndarray, n_splits: int = 4,
    nombre: str = "modelo", progress: bool = True,
) -> SimpleNamespace:
    """Grid search con CV temporal para un modelo, con **barra de progreso**.

    Recorre cada combinación de ``spec["grid"]`` y la evalúa con ``cross_val_score``
    sobre los folds temporales (RMSE promedio); el ``Pipeline`` (one-hot +
    imputación [+ escalado] + modelo) se reajusta **por fold**. La barra (``tqdm``)
    avanza una vez por combinación y muestra el mejor RMSE hasta el momento.
    Reentrena el mejor estimador en **todo** train.

    Devuelve un objeto con ``best_params_``, ``best_score_`` (= -RMSE de CV) y
    ``best_estimator_`` (Pipeline ajustado), compatible con el resto del módulo.
    """
    folds = time_series_folds(periodos_train, n_splits=n_splits)
    feature_cols = list(X_train.columns)
    combos = list(ParameterGrid(spec["grid"]))
    n_jobs = spec.get("search_n_jobs", 1)

    resultados, mejor_rmse = [], np.inf
    barra = tqdm(combos, desc=f"{nombre} ({len(combos)} combos)", disable=not progress)
    for params in barra:
        pipe = build_pipeline(clone(spec["estimator"]), spec["scale"], feature_cols).set_params(**params)
        scores = cross_val_score(
            pipe, X_train, y_train, cv=folds,
            scoring=RMSE_SCORER, n_jobs=n_jobs,
        )
        cv_rmse = float(-scores.mean())
        resultados.append({"params": params, "cv_rmse": cv_rmse})
        mejor_rmse = min(mejor_rmse, cv_rmse)
        barra.set_postfix(rmse=f"{cv_rmse:.2f}", mejor=f"{mejor_rmse:.2f}")

    best = min(resultados, key=lambda r: r["cv_rmse"])
    best_estimator = build_pipeline(
        clone(spec["estimator"]), spec["scale"], feature_cols
    ).set_params(**best["params"])
    best_estimator.fit(X_train, y_train)
    return SimpleNamespace(
        best_params_=best["params"],
        best_score_=-best["cv_rmse"],
        best_estimator_=best_estimator,
        cv_results_=resultados,
    )


def tune_all(
    ds: pd.DataFrame, feature_cols: list[str], n_splits: int = 4,
    progress: bool = True,
) -> tuple[pd.DataFrame, dict]:
    """Tunea ridge, random_forest y xgboost con CV temporal sobre train.

    Cada modelo muestra su propia barra de progreso. Devuelve ``(tabla_cv,
    searches)``: la tabla con el RMSE de CV y los mejores hiperparámetros de cada
    modelo, y el dict de resultados de búsqueda.
    """
    X_tr, y_tr, periodos = train_search_arrays(ds, feature_cols)
    searches, filas = {}, []
    for nombre, spec in search_spaces().items():
        search = tune_model(
            spec, X_tr, y_tr, periodos, n_splits=n_splits,
            nombre=nombre, progress=progress,
        )
        searches[nombre] = search
        filas.append({
            "modelo": nombre,
            "cv_rmse": -search.best_score_,
            "best_params": search.best_params_,
        })
    tabla = pd.DataFrame(filas).sort_values("cv_rmse").reset_index(drop=True)
    return tabla, searches


def eval_searches_on_val(
    searches: dict, ds: pd.DataFrame, feature_cols: list[str],
) -> pd.DataFrame:
    """Evalúa el mejor estimador de cada búsqueda sobre **val** (held-out, intacto
    durante el tuning) más la persistencia. Tabla comparativa ordenada por val_rmse.
    """
    _, _, X_val, y_val = split_train_val(ds, feature_cols)
    filas = []
    for nombre, search in searches.items():
        pred = search.best_estimator_.predict(X_val)
        filas.append({"modelo": nombre, **evaluate(y_val, pred)})
    filas.append({"modelo": "persistencia (baseline)", **persistence_val(ds)})
    return pd.DataFrame(filas).sort_values("val_rmse").reset_index(drop=True)


def eval_searches_train_val(
    searches: dict, ds: pd.DataFrame, feature_cols: list[str],
) -> pd.DataFrame:
    """Toma la **mejor config** de cada búsqueda y mide RMSE/R² en **train y val**.

    El ``best_estimator_`` ya viene reentrenado en todo train. Reportar las dos
    métricas a la vez sirve para ver el **gap train→val** (sobreajuste): si train
    es mucho mejor que val, el modelo memoriza. Incluye la persistencia en ambos
    splits como referencia. Ordena por ``val_rmse``.
    """
    X_tr, y_tr, X_va, y_va = split_train_val(ds, feature_cols)

    filas = []
    for nombre, search in searches.items():
        est = search.best_estimator_
        m_tr = evaluate(y_tr, est.predict(X_tr))
        m_va = evaluate(y_va, est.predict(X_va))
        filas.append({
            "modelo": nombre,
            "train_rmse": m_tr["val_rmse"], "train_r2": m_tr["val_r2"],
            "val_rmse": m_va["val_rmse"], "val_r2": m_va["val_r2"],
        })

    # persistencia (ŷ = prod_pet(t)) en cada split, como referencia
    usable = ds[ds[TARGET_COL].notna()]
    p_tr = evaluate(*_persistencia(usable, "train"))
    p_va = evaluate(*_persistencia(usable, "val"))
    filas.append({
        "modelo": "persistencia (baseline)",
        "train_rmse": p_tr["val_rmse"], "train_r2": p_tr["val_r2"],
        "val_rmse": p_va["val_rmse"], "val_r2": p_va["val_r2"],
    })
    return pd.DataFrame(filas).sort_values("val_rmse").reset_index(drop=True)


def _persistencia(usable: pd.DataFrame, split: str) -> tuple[pd.Series, pd.Series]:
    """(y_true, y_pred) de la persistencia para un split: ŷ(t+1) = prod_pet(t)."""
    s = usable[usable.split == split]
    return s[TARGET_COL], s["prod_pet"]
