# Feature Store — contrato (Fase 3)

> **Dueño:** Rol 2 (Feature Store + Orquestación). **Consumidores:** Rol 1 (entrenamiento) y Rol 3 (inferencia).
> Contrato estable del feature store. Decisión y alternativas: [ADR-036](adr/0036-feature-store.md). Diseño del problema: [ADR-028](adr/0028-diseno-problema-modelado.md).

## Por qué existe

La consigna de Fase 3 (RNF) exige que **el procesamiento y la generación de features quede persistido en un feature store** y que **las mismas features sirvan para entrenamiento e inferencia**, para evitar el **training-serving skew**: si el modelo se entrena con features calculadas de una forma y la API las recalcula de otra, las predicciones en producción difieren de las de entrenamiento. La solución: **calcular las features una sola vez** y que training e inferencia **lean lo mismo**.

## Qué es (realineado en Fase 3 — ver Revisión en ADR-036)

Una tabla en el DW (PostgreSQL) que **materializa la salida del pipeline de features de ML** (`ml/features.py` + `ml/dataset.py`), es decir, **exactamente las features que consume el modelo campeón** ([ADR-040](adr/0040-modelo-produccion.md)). La produce un **asset de Dagster** (Rol 2) que ejecuta ese pipeline y escribe la tabla; **no** se reimplementa en SQL/dbt (las features de A —KNN de vecinos, lags por calendario— no se expresan bien en SQL y se desincronizarían).

### Dos modelos → dos tablas (ADR-042)

Desde la Fase 3 se pronostican **dos targets**: petróleo (`prod_pet`) y gas (`prod_gas`), un modelo por target ([ADR-042](adr/0042-modelo-prediccion-gas.md)). El store materializa **una tabla por target** (decisión registrada en ADR-036, *Revisión 2*): cada una con **su universo** (pozos con ese target `> 0` en train — el gasífero es más amplio), **sus features de ingeniería** sobre el target (`prod_pet_*` vs `prod_gas_*`) y su `y_next`. Las 8 numéricas base y las 14 categóricas son **compartidas** (se duplican entre tablas; trivial al volumen). Rol 3 lee **la tabla del target** que sirve.

| | Petróleo (`prod_pet`) | Gas (`prod_gas`) |
|---|---|---|
| **Tabla** | `features.feat_produccion_pozo_mensual` (nombre histórico) | `features.feat_produccion_pozo_mensual_gas` |
| **Universo** | pozos con `prod_pet > 0` en train | pozos con `prod_gas > 0` en train (más amplio) |
| **Engineered** | `prod_pet_roll3/delta1/lag12/acum6` + genéricas | `prod_gas_roll3/delta1/lag12/acum6` + genéricas |
| **Experimento / modelo MLflow** | `produccion-forecast` | `produccion-forecast-gas` |

Todo lo demás del contrato es **idéntico** entre las dos tablas (grano, claves, encuadre temporal, categóricas crudas, `y_next` nullable para serving). Donde abajo dice "la tabla", aplica a cada una.

| | |
|---|---|
| **Tablas** | `features.feat_produccion_pozo_mensual` (petróleo) · `features.feat_produccion_pozo_mensual_gas` (gas) |
| **Productor** | el job de retrain (asset `features_refrescadas`) corre el pipeline de `ml/` para los dos targets y materializa ambas tablas (única fuente de verdad de las features) |
| **Grano** | una fila por `(idpozo, mes base t)` |
| **Clave de lookup (inferencia)** | `idpozo` + `periodo` (primer día del mes base `t`) |
| **Consumidores** | Rol 1 (training, mismas features) y Rol 3 (`/predict`, por target) |
| **Refresh** | lo materializa el **job de retrain** antes de entrenar (ADR-041). **No** es parte del refresh del DW (`dw_publish`), para no acoplar el pipeline de datos a las deps de `ml/`. |

## Encuadre temporal (fila = mes base `t`)

Cada fila es el **mes base `t`**: todas las features están disponibles al cierre de `t` (anti-leakage) y sirven para predecir el **target** (`prod_pet` o `prod_gas`) del **mes siguiente `t+1`**. Para inferir `t+1`, la API lee la fila del mes base `t = (t+1) − 1 mes` **en la tabla del target pedido**.

> ⚠️ **Sutileza del feature `mes`:** entre las features del modelo, `mes` es el **mes del target (`t+1`)**, no el del mes base (lo fija `build_basic_dataset`: `mes = periodo_objetivo.month`). Por eso el lookup de inferencia usa `periodo` (mes base), no la columna `mes`. Mantener esa distinción al leer.

## Columnas (contrato)

El store materializa **solo el set recursion-safe** (ADR-043): las **35 features** que usa el modelo. La **fuente de verdad** es el código de ML (`ml.dataset.BASIC_NUMERIC_FEATURES` + `ml.features.engineered_feature_names(target)` + `ml.dataset.BASIC_CATEGORICAL_FEATURES`, filtrado por `ml.modeling.recursion_safe_cols`). Cada tabla tiene **39 columnas** (3 claves + 35 features + `y_next`). Las **autorregresivas cambian de nombre por target** (prefijo `prod_pet_` / `prod_gas_`); las categóricas son idénticas:

- **Claves / lookup:** `idpozo` (bigint), `periodo` (date, mes base), `periodo_objetivo` (date, `t+1`).
- **Numéricas (5):** `{target}` (nivel del mes `t`), `profundidad`, `coordenadax`, `coordenaday`, `mes` (= mes del target).
- **Engineered (16):** autorregresivas con prefijo del target — `{target}_roll3`, `{target}_roll6`, `{target}_delta1`, `{target}_delta3`, `{target}_ratio1`, `{target}_lag2`, `{target}_lag3`, `{target}_lag12`, `{target}_acum6`, `{target}_acum12`, `{target}_std3`, `{target}_cummax`, `{target}_frac_peak`, `{target}_meses_desde_pico` — + genéricas `produjo_mes_pasado`, `well_age_months`.
- **Categóricas crudas (14):** `tipoextraccion`, `tipoestado`, `tipopozo`, `empresa`, `formprod`, `formacion`, `areapermisoconcesion`, `areayacimiento`, `cuenca`, `provincia`, `proyecto`, `clasificacion`, `subclasificacion`, `sub_tipo_recurso`.
- **Target:** `y_next` (float, nullable).

> El store **no** materializa columnas de metadata (`feature_set_version` / `computed_at`): el versionado del contrato se lleva por este documento + git (ver *Versionado*), no por columnas en la tabla.

> Las categóricas van **crudas**: el one-hot/imputación/escalado vive en el `Pipeline` del modelo (se ajusta solo en train, [ADR-039](adr/0039-preprocesamiento-datos.md)) y se serializa en el artefacto, para que la inferencia lo replique idéntico. El store **no** encodea.

> El **target `y_next`** es una columna de **entrenamiento**, no de serving: las filas de serving (mes base más reciente) no lo tienen. El training lo arma por merge de calendario (`periodo + 1 mes`).

## Cómo consumir

### Rol 1 — entrenamiento
El store **materializa el pipeline de `ml/`** (el asset `features_refrescadas` reusa `ml.features.add_engineered_features(target=...)` + las listas `ml.dataset.BASIC_*`), con **paridad validada** contra `build_basic_dataset` para **los dos targets** (0 diferencias). El training puede seguir usando `build_basic_dataset(target=...)` (mismas features) o leer la tabla del target (filas con `y_next` no nulo). Como la lista de features sale del código de `ml/`, cambiarla ahí re-materializa ambas tablas sin reescribir nada. `ml/requirements.txt` ya existe (Rol 1) → el venv del daemon instala las deps de `ml/`.

### Rol 3 — inferencia (`GET /api/v1/forecast`, recursivo)
La inferencia se unificó en `/forecast` (recursivo mensual, ADR-044); `/predict` se retiró (ADR-035 reemplazado). El forecast lee del store (`feature_reader.get_history_for_forecast`, **implementado**): (1) la **fila del mes base** (último mes del pozo, con `y_next` NULL — el store la conserva vía left-join) → predice t+1 **sin recalcular**; (2) la **serie** `(periodo, <target>)` de todos los meses → recalcula los meses futuros (t+2+). Elige la **tabla del target** (`feat_produccion_pozo_mensual` / `..._gas`) y descarta `idpozo/periodo/periodo_objetivo/y_next` (guarda anti-leak). **Dependencia operativa:** el store debe estar **re-materializado** con las columnas recursion-safe (ADR-043) — lo hace el retrain (ADR-041) reusando `ml.features`, así que la paridad training-serving está garantizada.

## Versionado y cambios

Cambiar la lista de features del modelo (en `ml/`) es un **cambio de contrato**: se documenta acá (sección *Columnas*, con su fecha/commit) y se re-materializa el store. Como la lista sale del código de `ml/`, el asset de materialización la toma automáticamente para los dos targets; avisar a Rol 3 para que ajuste el reader si cambian las columnas. Un **target nuevo** (otra producción) = una tabla nueva (`table_for(target)` en `feature_store_build.py`) + sumarlo a `ml.config.TARGETS`.
