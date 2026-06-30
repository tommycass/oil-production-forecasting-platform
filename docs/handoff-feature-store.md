# Handoff — feature store materializado (Fase 3)

> **De:** Rol 2 (Feature Store + Orquestación). **Para:** Rol 1 (entrenamiento) y Rol 3 (inferencia).
> Acompaña al contrato [docs/feature-store.md](feature-store.md) y a [ADR-036](adr/0036-feature-store.md) (sección *Revisión*). Responde al handoff de Rol 1 en [docs/handoffs/rol2-feature-store.md](handoffs/rol2-feature-store.md).

## Qué hicimos

Materializamos el feature store con **exactamente las features del modelo** (las 29), **reusando el código de `ml/`** como pide el handoff de Rol 1 (única fuente de verdad → cero skew). **Son dos modelos (ADR-042) → dos tablas, una por target** (decisión registrada en ADR-036 *Revisión 2*):

- Un asset de Dagster (`features_refrescadas`, en el job de retrain) corre el pipeline de features (`data_pipeline/orchestration/feature_store_build.py`, que importa `ml.features.add_engineered_features(target=...)` y las listas `ml.dataset.BASIC_*`) y escribe **las dos tablas**:
  - `features.feat_produccion_pozo_mensual` — petróleo (`prod_pet`, nombre histórico).
  - `features.feat_produccion_pozo_mensual_gas` — gas (`prod_gas`).
- Cada tabla tiene **su universo** (pozos con ese target `> 0` en train; el gasífero es más amplio), **sus engineered** sobre el target (`prod_pet_*` vs `prod_gas_*`) y su `y_next`. Las 8 numéricas base + 14 categóricas son compartidas (se duplican). `build_store_features(df, target=...)` se parametrizó por target; `materializar_todos` recorre `ml.config.TARGETS`.
- **Fuente:** `bronze.produccion` (crudo, mismas columnas que el CSV del training; gobernado en el DW). Se lee **una sola vez** y se construyen las dos tablas.
- **Paridad validada (§8 del handoff de Rol 1):** comparado contra `build_basic_dataset(target=...)` para **los dos targets** → **0 diferencias** en las 29 features ni en `y_next`. Cada tabla = 33 columnas (3 claves + 29 features + `y_next`), tipos correctos.
- **Diferencia con training:** el target va por **left-join**, así se conserva la **última fila de cada pozo** (`y_next` NULL) para que la API pueda predecir el mes siguiente. El training usa las filas con `y_next` no nulo (idéntico a `build_basic_dataset`).

## Pendiente de Rol 1

1. ~~Agregar `ml/requirements.txt`~~ **HECHO** (ya está en el repo: `mlflow`, `scikit-learn`, `pandas`, `numpy`, `xgboost`, …). El venv del daemon de retrain lo instala (la materialización del store y el training reusan `ml/`).
2. **Cuando quieras, conectá el training al store:** podés seguir usando `build_basic_dataset(target=...)` (mismas features, ya validado para ambos targets) o leer la tabla del target (filas con `y_next` no nulo). Si cambiás la lista de features en `ml/`, el asset la toma automáticamente (importa tus listas) — solo avisanos para re-materializar y que C ajuste el reader.

## Pendiente de Rol 3 (inferencia)

`api/app/services/feature_reader.py` hoy lee **6 columnas viejas** (`lag1, lag2, lag3, roll3, antiguedad, tef_lag1`) de **una sola tabla** — todo desactualizado. Actualizar a las **29 columnas del modelo** y **parametrizar la tabla por target** (ver lista en [docs/feature-store.md](feature-store.md)):

- **Elegir la tabla del target:** `feat_produccion_pozo_mensual` (petróleo) / `feat_produccion_pozo_mensual_gas` (gas), y el modelo `Production` correspondiente (`produccion-forecast` / `produccion-forecast-gas`).
- **Lookup:** por `idpozo` + `periodo` del **mes base** `t` (= primer día de `mes_objetivo − 1 mes`), no por la columna `mes`.
- **Devolver todas las features** (numéricas + engineered + categóricas **crudas**) y pasárselas al `Pipeline` del modelo `Production` (el Pipeline hace one-hot/imputación internamente). Ojo: los nombres engineered difieren por target (`prod_pet_*` vs `prod_gas_*`).
- ⚠️ `mes` es el mes del **target** (`t+1`), no el del mes base; por eso el lookup va por `periodo`.
- **Cómo exponer el target en `/predict`** (un parámetro `target` o dos rutas) lo decidís vos; conviene revisar [ADR-035] (contrato de `/predict`) como anota el ADR-042.
