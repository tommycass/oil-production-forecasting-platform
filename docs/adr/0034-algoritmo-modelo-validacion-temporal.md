# Título: ADR-034: Algoritmo del modelo y validación/tuning temporal

**Estado:** Aceptada

## Contexto

El ADR-029 fijó la **vara de éxito** (la persistencia: un modelo solo se justifica si la supera) y remitió el algoritmo a un ADR aparte (ADR-029: *"el algoritmo… se documenta en ADR-034"*). Este ADR toma esa decisión: **qué familia de modelo** se usa y **cómo se valida y tunea** sin leakage temporal.

El problema (ADR-028) es una **regresión tabular global** de `prod_pet(t+1)`. El dataset (ADR-031/033) tiene ~380 columnas (mayormente dummies ralas del one-hot, ADR-032), target muy asimétrico (cola larga + ~25% de ceros) y fuerte autocorrelación. La comparación se hace **entrenando en train y midiendo en val** (ADR-028); `test` queda intacto. Métricas: **RMSE** (m³, penaliza errores grandes) y **R²**.

La implementación reutilizable vive en `ml/modeling.py` y la comparación (tuning + campeón) en `notebooks/02_feature_selection_pet.ipynb` (petróleo) y `notebooks/03_feature_selection_gas.ipynb` (gas).

## Análisis de Alternativas

### A. Familia de modelo

Comparación en **val** (entrenando en train), con preprocesamiento ajustado solo en train (imputación **por feature** —esquema definido en ADR-038, con `0 + flag` en volúmenes/lags y mediana en físicas—; estandarización solo para el lineal). Configuración inicial (sin tuning fino):

| Modelo | val RMSE (m³) | val R² | Comentario |
|---|---|---|---|
| **XGBoost** (gradient boosting) | **238,5** | **0,893** | mejor; captura no-linealidades e interacciones |
| Regresión lineal / Ridge (L2) | 242,8 | 0,889 | fuerte y barata; supera la persistencia |
| Persistencia (baseline, ADR-029) | 250,6 | 0,881 | referencia a batir |
| Random Forest | 251,9 | 0,880 | por debajo del baseline con esta config |

- **Regresión lineal / Ridge:** simple, interpretable y rápida; con ~380 dummies ralas conviene **regularización L2** (Ridge) para controlar varianza. Compite bien, pero no modela interacciones ni no-linealidades.
- **Random Forest:** maneja no-linealidades sin escalado, pero con muchas dummies ralas + `max_features="sqrt"` + hojas grandes **promedia de más** y queda por debajo del baseline; mejora con tuning, pero parte en desventaja.
- **XGBoost (gradient boosting):** mejor RMSE/R², maneja no-linealidades, interacciones y NaN, y con `tree_method="hist"` entrena rápido. Es el candidato más fuerte.
- **Redes neuronales (descartado):** para datos tabulares de este tamaño rara vez superan al boosting, y agregan complejidad de entrenamiento/serving sin beneficio claro. Queda fuera del alcance.

> Nota: los R² son todos altos (~0,88–0,89) porque `prod_pet(t)` ya explica casi toda la varianza (autoregresión); el margen sobre la persistencia es chico y se juega en los errores grandes (RMSE). Los números son la corrida inicial; el tuning (sección B) ajusta los hiperparámetros finales.

### B. Validación y tuning: K-fold estándar vs CV temporal

El tuning de hiperparámetros necesita validación cruzada, pero **al ser serie temporal el K-fold por defecto es inválido**:

- **`KFold` aleatorio (descartado):** baraja las filas, así que algunos folds entrenarían con meses **posteriores** a los que validan → **leakage futuro→pasado**. Da métricas optimistas e irreales.
- **CV temporal *expanding window* por mes (elegido):** `modeling.time_series_folds` arma folds donde el bloque de validación es siempre **posterior** a todo el train del fold (estilo `TimeSeriesSplit`, pero a nivel de mes para respetar el panel pozo×mes). Nunca se entrena con el futuro de lo que se valida.

Además, **todo el preprocesamiento que aprende de los datos vive dentro de un `Pipeline`** — **one-hot** (con fallback `DESCONOCIDO`, vía `ColumnTransformer` + `OneHotDESC`), **imputación** por mediana y **escalado** — para que la CV lo **reajuste solo con el train de cada fold**. La alternativa (encodear/imputar/escalar todo el train antes de la CV) filtraría estadísticos —incluido el **vocabulario de categorías**— entre folds (leakage val→train dentro de la búsqueda). Verificado: en cada fold, las categorías que solo aparecen en su tramo de validación **no** entran al vocabulario del fold (caen en `DESCONOCIDO`).

### C. Codificación del target

- **Target en escala original (elegido para el baseline de modelado):** directo, métricas interpretables en m³.
- **`log1p(target)` (descartado, ver ADR-038):** se evaluó tras fijar el algoritmo y **empeora** el RMSE (rompe la relación casi lineal `prod_pet(t) ≈ prod_pet(t+1)`; ridge val RMSE 496,6 vs 242,8). El target se deja en escala original.

## Decisión

1. **Familias evaluadas: Ridge (L2), Random Forest y XGBoost.** La comparación en val (tabla arriba) muestra que árboles y boosting capturan no-linealidades e interacciones que el lineal no. El **campeón se fija tras el tuning de hiperparámetros en ADR-039** (Random Forest en ambos targets, `ml/train.py::CHAMPION = "random_forest"`); Ridge se mantiene como comparador simple e interpretable.
2. **Validación/tuning con CV temporal** (*expanding window* por mes) + **`Pipeline`** que reajusta el preprocesamiento por fold. Se tunea con **random search** (`ParameterSampler`): cada hiperparámetro es una lista de valores con sentido (3–5) y se muestrean **`n_iter` configuraciones** por modelo (controla el tiempo), evaluando cada una con `cross_val_score`:
   - Ridge: la `alpha` (lambda L2).
   - Random Forest y XGBoost: profundidad (acotada, sin `None`), learning rate, n_estimators, min_samples_leaf / min_child_weight, subsample, etc.
   Los hiperparámetros finales salen de esta búsqueda; el scoring es **RMSE** (`neg`).
3. **Criterio de promoción (model registry, MLflow):** el criterio definitivo lo fija ADR-039 y lo implementa `ml/registry.py::promotion_decision`: un modelo pasa a Production solo si **supera a la persistencia en test RMSE** y **mejora al Production vigente** (o es el primer campeón). El run, los params, las métricas y el modelo quedan registrados en MLflow (ADR-030) para comparación y reproducibilidad.

## Consecuencias

**Positivas:**
- Decisión de algoritmo **respaldada por evidencia** (tabla en val) y no por preferencia.
- Tuning **sin leakage temporal** (CV expanding-window + Pipeline por fold), defendible para serie temporal.
- Criterio de promoción **objetivo** (batir la persistencia en RMSE), encadenable con el registry y el serving.

**Negativas / trade-offs:**
- XGBoost es **menos interpretable** que la regresión lineal; se mitiga manteniendo Ridge como comparador y, a futuro, importancias/SHAP.
- El margen sobre la persistencia es **acotado** (autocorrelación alta): hay que demostrar que la ganancia justifica la complejidad de servir un modelo de boosting.
- El tuning con CV temporal es **más caro** que un split simple (varios folds × `n_iter` configuraciones), sobre todo para Random Forest; el random search con `n_iter` acota ese costo.
- La elección queda atada al dataset/split de los ADR-028/031; si cambian, hay que re-tunear y recomparar.

---

> Relacionados: **ADR-029** (baseline y vara de éxito, que prometió este ADR), **ADR-028** (encuadre y split temporal), **ADR-031/033** (dataset y features que consume el modelo), **ADR-030** (MLflow: tracking + registry donde se versiona y promueve el campeón).
