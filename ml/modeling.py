"""Entrenamiento y comparación de modelos para el forecast de ``prod_pet`` (t+1).

Funciones reutilizables para:
- armar la matriz de features ``X`` / target ``y`` desde el dataset (one-hot de
  categóricas ajustado en train, vía ``ml.dataset.onehot_encode_dataset``),
- **preprocesar** (imputación de NaN y normalización) **ajustando siempre en
  train** y aplicando a val/test (anti-leakage val→train),
- entrenar y evaluar (RMSE, R²) modelos comparables sobre val.

Se entrena en ``train`` y se compara en ``val`` (ADR-028); ``test`` queda intacto.
Métricas: RMSE (m³) y R² (varianza explicada).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score, root_mean_squared_error
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

from ml import dataset

# columnas que NO son features (claves, target)
KEYS = ["idpozo", "periodo", "periodo_objetivo", "split"]
TARGET_COL = "y_next"

# modelos que requieren normalización (escala) de las features
NEEDS_SCALING = {"reg_lineal"}


# --- Matriz de features y split --------------------------------------------

def build_feature_matrix(ds: pd.DataFrame | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Devuelve ``(ds_encoded, feature_cols)``: el dataset con las categóricas
    en one-hot (ajustado en train) y la lista de columnas que son features.

    Si no se pasa ``ds``, se construye con ``ml.dataset.build_basic_dataset``.
    """
    if ds is None:
        ds = dataset.build_basic_dataset()
    ds_enc, _ = dataset.onehot_encode_dataset(ds)
    feature_cols = [c for c in ds_enc.columns if c not in KEYS + [TARGET_COL]]
    return ds_enc, feature_cols


def split_train_val(
    ds_enc: pd.DataFrame, feature_cols: list[str]
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
    """Separa en (X_train, y_train, X_val, y_val) según la columna ``split``."""
    usable = ds_enc[ds_enc[TARGET_COL].notna()]
    tr = usable[usable.split == "train"]
    va = usable[usable.split == "val"]
    return (
        tr[feature_cols], tr[TARGET_COL],
        va[feature_cols], va[TARGET_COL],
    )


# --- Preprocesamiento (siempre fit en train) -------------------------------

def impute_train_val(
    X_train: pd.DataFrame, X_val: pd.DataFrame, strategy: str = "median"
) -> tuple[np.ndarray, np.ndarray, SimpleImputer]:
    """Imputa NaN ajustando el estadístico **solo en train** (anti-leakage).

    Necesario para RandomForest y regresión lineal (no toman NaN). Devuelve las
    matrices imputadas (float32) y el imputador ajustado (reutilizable en test).
    """
    imp = SimpleImputer(strategy=strategy).fit(X_train)
    Xtr = imp.transform(X_train).astype("float32")
    Xva = imp.transform(X_val).astype("float32")
    return Xtr, Xva, imp


def normalize_train_val(
    X_train: np.ndarray, X_val: np.ndarray
) -> tuple[np.ndarray, np.ndarray, StandardScaler]:
    """Estandariza (media 0, desvío 1) ajustando **solo en train**. Para la
    regresión lineal (sensible a la escala de las features). Devuelve las
    matrices escaladas y el scaler ajustado.

    Importante: ajustar en train y aplicar a val/test evita el leakage de
    estadísticos (media/desvío) de val/test hacia el entrenamiento.
    """
    scaler = StandardScaler().fit(X_train)
    return scaler.transform(X_train).astype("float32"), scaler.transform(X_val).astype("float32"), scaler


# --- Modelos y evaluación --------------------------------------------------

def get_models(random_state: int = 42) -> dict:
    """Modelos a comparar, con hiperparámetros razonables para un primer barrido."""
    return {
        "reg_lineal": LinearRegression(),
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


def evaluate(y_true, y_pred) -> dict[str, float]:
    """RMSE (m³, penaliza errores grandes) y R² (varianza explicada)."""
    return {
        "val_rmse": root_mean_squared_error(y_true, y_pred),
        "val_r2": r2_score(y_true, y_pred),
    }


def persistence_val(ds_enc: pd.DataFrame) -> dict[str, float]:
    """Baseline de persistencia en val: ŷ(t+1) = prod_pet(t). Referencia a batir
    (ADR-029); ``prod_pet`` en el dataset es justo la producción del mes t."""
    va = ds_enc[(ds_enc.split == "val") & ds_enc[TARGET_COL].notna()]
    return evaluate(va[TARGET_COL], va["prod_pet"])


def train_eval_models(
    X_train: pd.DataFrame, y_train: pd.Series,
    X_val: pd.DataFrame, y_val: pd.Series,
    models: dict | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Entrena cada modelo en train y lo evalúa en val.

    Preprocesa una sola vez: matriz **imputada** (para árboles) y matriz
    **imputada + normalizada** (para los modelos en ``NEEDS_SCALING``). Devuelve
    ``(tabla_resultados ordenada por val_rmse, modelos_entrenados)``.
    """
    models = models or get_models()

    Xtr_i, Xva_i, _ = impute_train_val(X_train, X_val)
    Xtr_s, Xva_s, _ = normalize_train_val(Xtr_i, Xva_i)

    filas, entrenados = [], {}
    for nombre, modelo in models.items():
        Xtr, Xva = (Xtr_s, Xva_s) if nombre in NEEDS_SCALING else (Xtr_i, Xva_i)
        modelo.fit(Xtr, y_train)
        filas.append({"modelo": nombre, **evaluate(y_val, modelo.predict(Xva))})
        entrenados[nombre] = modelo

    tabla = pd.DataFrame(filas).sort_values("val_rmse").reset_index(drop=True)
    return tabla, entrenados
