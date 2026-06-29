# Handoff — feature store materializado (Fase 3)

> **De:** Rol 2 (Feature Store + Orquestación). **Para:** Rol 1 (entrenamiento) y Rol 3 (inferencia).
> Acompaña al contrato [docs/feature-store.md](feature-store.md) y a [ADR-036](adr/0036-feature-store.md) (sección *Revisión*). Responde al handoff de Rol 1 en [docs/handoffs/rol2-feature-store.md](handoffs/rol2-feature-store.md).

## Qué hicimos

Materializamos el feature store con **exactamente las features del modelo** (las 29), **reusando el código de `ml/`** como pide el handoff de Rol 1 (única fuente de verdad → cero skew):

- Un asset de Dagster (`feature_store`) corre el pipeline de features (`data_pipeline/orchestration/feature_store_build.py`, que importa `ml.features.add_engineered_features` y las listas `ml.dataset.BASIC_*`) y escribe `features.feat_produccion_pozo_mensual`.
- **Fuente:** `bronze.produccion` (crudo, mismas columnas que el CSV del training; gobernado en el DW).
- **Paridad validada (§8 del handoff de Rol 1):** comparado contra `build_basic_dataset` sobre la misma muestra → **0 diferencias** en las 29 features ni en `y_next` (6353 filas etiquetadas). Materialización a Postgres OK (33 columnas, tipos correctos).
- **Reemplaza** el modelo dbt anterior del store (las 6 features viejas): el store ahora lo produce este asset Python.
- **Diferencia con training:** el target va por **left-join**, así se conserva la **última fila de cada pozo** (`y_next` NULL) para que la API pueda predecir el mes siguiente. El training usa las filas con `y_next` no nulo (idéntico a `build_basic_dataset`).

## Pendiente de Rol 1

1. **Agregar `ml/requirements.txt`** (`mlflow`, `scikit-learn`, `pandas`, `numpy`, …) — **urgente para no romper el DW**: el asset `feature_store` se materializa dentro de `dw_publish` (reusa `ml/`), así que **el venv del DW (`run_pipeline.sh`) y el del daemon de retrain ahora necesitan las deps de `ml/`**. Sin ellas, la próxima corrida de `dw_publish` falla al importar `ml` en la materialización. Hasta que exista el archivo, instalar a mano `pip install mlflow scikit-learn pandas numpy` en esos venvs.
2. **Cuando quieras, conectá el training al store:** podés seguir usando `build_basic_dataset` (mismas features, ya validado) o leer `features.feat_produccion_pozo_mensual` (filas con `y_next` no nulo). Si cambiás la lista de features en `ml/`, el asset la toma automáticamente (importa tus listas) — solo avisanos para re-materializar y que C ajuste el reader.

## Pendiente de Rol 3 (inferencia)

`api/app/services/feature_reader.py` hoy lee **6 columnas viejas** (`lag1, lag2, lag3, roll3, antiguedad, tef_lag1`) que **ya no existen**. Actualizar a las **29 columnas del modelo** (ver lista en [docs/feature-store.md](feature-store.md)):

- **Lookup:** por `idpozo` + `periodo` del **mes base** `t` (= primer día de `mes_objetivo − 1 mes`), no por la columna `mes`.
- **Devolver todas las features** (numéricas + engineered + categóricas **crudas**) y pasárselas al `Pipeline` del modelo `Production` (el Pipeline hace one-hot/imputación internamente).
- ⚠️ `mes` es el mes del **target** (`t+1`), no el del mes base; por eso el lookup va por `periodo`.
