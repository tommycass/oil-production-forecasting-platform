"""Preprocesamiento por feature para el forecast (imputación de NaN), anti-leakage.

Todos los transformers aprenden sus estadísticos en ``fit`` (mediana, vocabulario
one-hot) y, al vivir dentro de un ``Pipeline`` / ``ColumnTransformer``,
``cross_val_score``/``GridSearchCV`` los **reajustan solo con el train de cada
fold**. Nunca se computa un estadístico sobre val/test ni sobre el futuro: el train
son filas ``periodo <= TRAIN_END`` (pasado).

**Outliers:** NO se clippean ni se transforman. Empíricamente, recortar o
transformar (clip / log1p) los volúmenes de producción **empeora** el modelo lineal
y es **neutro** para los árboles (transformación monótona): en RMSE sobre un target
de cola pesada, los pozos grandes dominan el error y su producción extrema **es
señal**, no ruido (ADR-036). Los **errores de dato** (producción negativa) se
**descartan** en ``ml.dataset.build_basic_dataset``, no se imputan.

Tratamiento de NaN por grupo de features (ADR-036):

| Grupo             | Features                                                     | NaN              |
|-------------------|-------------------------------------------------------------|------------------|
| Volúmenes         | prod_pet, prod_gas, prod_agua, roll3, acum6, lag12, vecinos | 0 + flag         |
| Variación         | prod_pet_delta1                                              | 0 + flag         |
| Ratios / flags    | water_cut, produjo_mes_pasado                               | 0                |
| Físicas estáticas | profundidad, coordenadax, coordenaday                       | mediana (train)  |
| Operativa         | tef                                                         | mediana (train)  |
| Calendario        | mes                                                         | —                |
| Categóricas       | tipoextraccion, ... (14)                                     | DESCONOCIDO      |
"""
from __future__ import annotations

import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.impute import MissingIndicator, SimpleImputer
from sklearn.preprocessing import OneHotEncoder

from ml import dataset

# --- Grupos de features (por nombre; se intersecan con las presentes) ------
# La distinción importa para el NaN: en los volúmenes/variación un faltante es
# "no hay historia" (-> 0 + flag), mientras que en las físicas/operativa conviene
# la mediana de train.
VOLUMES = [
    "prod_pet", "prod_gas", "prod_agua",
    "prod_pet_roll3", "prod_pet_acum6", "prod_pet_lag12", "prod_vecinos_mean",
]
VARIATION = ["prod_pet_delta1"]
RATIOS_FLAGS = ["water_cut", "produjo_mes_pasado"]
STATIC_PHYSICAL = ["profundidad", "coordenadax", "coordenaday"]
OPERATIVA = ["tef"]
CALENDAR = ["mes"]
CATEGORICAL = dataset.BASIC_CATEGORICAL_FEATURES


# --- Transformer de one-hot con fallback (aprende el vocabulario en train) --

class OneHotDESC(BaseEstimator, TransformerMixin):
    """One-hot con fallback explícito ``DESCONOCIDO`` (ADR-032), apto para usar
    como paso de un ``Pipeline`` / ``ColumnTransformer``.

    A diferencia de ``ml.dataset.fit_onehot_encoder`` (que filtra ``split=="train"``),
    aprende el vocabulario en ``fit`` con **todas las filas que recibe** — que dentro
    de la cross-validation son las del **train de cada fold**. En ``transform``, los
    nulos y las **categorías no vistas** se mapean a la columna ``<feature>_DESCONOCIDO``.
    Así el one-hot también se ajusta por fold (sin leakage val→train en el vocabulario).
    """

    FILL = dataset.CATEGORICAL_NA_FILL  # "DESCONOCIDO"

    def fit(self, X, y=None):
        X = pd.DataFrame(X)
        self.columns_ = list(X.columns)
        categories = []
        for c in self.columns_:
            cats = sorted(X[c].fillna(self.FILL).unique().tolist())
            if self.FILL not in cats:
                cats.append(self.FILL)
            categories.append(cats)
        self.known_ = [set(c) for c in categories]
        self.encoder_ = OneHotEncoder(
            categories=categories, handle_unknown="ignore",
            sparse_output=False, dtype="uint8",
        ).fit(X[self.columns_].fillna(self.FILL))
        return self

    def transform(self, X):
        X = pd.DataFrame(X)[self.columns_].copy()
        for i, c in enumerate(self.columns_):
            X[c] = X[c].where(X[c].isin(self.known_[i]), self.FILL)
        return self.encoder_.transform(X)

    def get_feature_names_out(self, input_features=None):
        return self.encoder_.get_feature_names_out(self.columns_)


# --- Constructor del preprocesador -----------------------------------------

def _impute_const0() -> SimpleImputer:
    return SimpleImputer(strategy="constant", fill_value=0)


def build_preprocessor(feature_cols: list[str]) -> ColumnTransformer:
    """Arma el ``ColumnTransformer`` con el tratamiento de NaN por feature (ver
    tabla del módulo). Cada bloque se aplica a las columnas de su grupo que estén
    presentes; lo que aprende (mediana, vocabulario one-hot) se ajusta en ``fit``.

    El flag de faltante (``MissingIndicator``) va en una rama **paralela** sobre las
    columnas crudas (con NaN): marca con 0/1 dónde había NaN antes de imputar a 0.
    """
    def present(cols):
        return [c for c in cols if c in feature_cols]

    transformers = []

    vol = present(VOLUMES)
    if vol:
        transformers.append(("vol", _impute_const0(), vol))
        transformers.append(("vol_flag", MissingIndicator(features="all"), vol))

    var = present(VARIATION)
    if var:
        transformers.append(("var", _impute_const0(), var))
        transformers.append(("var_flag", MissingIndicator(features="all"), var))

    ratios = present(RATIOS_FLAGS)
    if ratios:
        transformers.append(("ratio", _impute_const0(), ratios))

    tef = present(OPERATIVA)
    if tef:
        transformers.append(("tef", SimpleImputer(strategy="median"), tef))

    static = present(STATIC_PHYSICAL)
    if static:
        transformers.append(("static", SimpleImputer(strategy="median"), static))

    cal = present(CALENDAR)
    if cal:
        transformers.append(("cal", "passthrough", cal))

    cat = present(CATEGORICAL)
    if cat:
        transformers.append(("cat", OneHotDESC(), cat))

    return ColumnTransformer(transformers=transformers, remainder="drop")
