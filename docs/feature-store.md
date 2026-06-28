# Feature Store — contrato (Fase 3)

> **Dueño:** Rol 2 (Feature Store + Orquestación). **Consumidores:** Rol 1 (entrenamiento) y Rol 3 (inferencia).
> Contrato estable del feature store. Decisión y alternativas: [ADR-036](adr/0036-feature-store.md). Diseño del problema: [ADR-028](adr/0028-diseno-problema-modelado.md).

## Por qué existe

La consigna de Fase 3 (RNF) exige que **el procesamiento y la generación de features quede persistido en un feature store** y que **las mismas features sirvan para entrenamiento e inferencia**, para evitar el **training-serving skew**: si el modelo se entrena con features calculadas de una forma y la API las recalcula de otra, las predicciones en producción difieren de las de entrenamiento. La solución: **calcular las features una sola vez** y que training e inferencia **lean lo mismo**.

## Qué es (realineado en Fase 3 — ver Revisión en ADR-036)

Una tabla en el DW (PostgreSQL) que **materializa la salida del pipeline de features de ML** (`ml/features.py` + `ml/dataset.py`), es decir, **exactamente las features que consume el modelo campeón** ([ADR-040](adr/0040-modelo-produccion.md)). La produce un **asset de Dagster** (Rol 2) que ejecuta ese pipeline y escribe la tabla; **no** se reimplementa en SQL/dbt (las features de A —KNN de vecinos, lags por calendario— no se expresan bien en SQL y se desincronizarían).

| | |
|---|---|
| **Tabla** | `features.feat_produccion_pozo_mensual` |
| **Productor** | asset Dagster que corre el pipeline de `ml/` (única fuente de verdad de las features) |
| **Grano** | una fila por `(idpozo, mes base t)` |
| **Clave de lookup (inferencia)** | `idpozo` + `periodo` (primer día del mes base `t`) |
| **Consumidores** | Rol 1 (training, mismas features) y Rol 3 (`/predict`) |
| **Refresh** | el job de retrain la materializa antes de entrenar (ADR-041); también con el refresh del DW |

## Encuadre temporal (fila = mes base `t`)

Cada fila es el **mes base `t`**: todas las features están disponibles al cierre de `t` (anti-leakage) y sirven para predecir `prod_pet` del **mes siguiente `t+1`**. Para inferir `t+1`, la API lee la fila del mes base `t = (t+1) − 1 mes`.

> ⚠️ **Sutileza del feature `mes`:** entre las features del modelo, `mes` es el **mes del target (`t+1`)**, no el del mes base (lo fija `build_basic_dataset`: `mes = periodo_objetivo.month`). Por eso el lookup de inferencia usa `periodo` (mes base), no la columna `mes`. Mantener esa distinción al leer.

## Columnas (contrato)

La **fuente de verdad** de la lista es el código de ML: `ml.dataset.BASIC_NUMERIC_FEATURES` + `ml.features.ENGINEERED_FEATURES` + `ml.dataset.BASIC_CATEGORICAL_FEATURES`. Al día de hoy son **29 features**:

- **Claves / lookup:** `idpozo`, `periodo` (date, mes base), `periodo_objetivo` (date, `t+1`).
- **Numéricas (8):** `prod_pet`, `prod_gas`, `prod_agua`, `tef`, `profundidad`, `coordenadax`, `coordenaday`, `mes` (= mes del target).
- **Engineered (7):** `prod_pet_roll3`, `prod_pet_delta1`, `prod_pet_lag12`, `prod_pet_acum6`, `water_cut`, `produjo_mes_pasado`, `prod_vecinos_mean`.
- **Categóricas crudas (14):** `tipoextraccion`, `tipoestado`, `tipopozo`, `empresa`, `formprod`, `formacion`, `areapermisoconcesion`, `areayacimiento`, `cuenca`, `provincia`, `proyecto`, `clasificacion`, `subclasificacion`, `sub_tipo_recurso`.
- **Metadata:** `feature_set_version`, `computed_at`.

> Las categóricas van **crudas**: el one-hot/imputación/escalado vive en el `Pipeline` del modelo (se ajusta solo en train, [ADR-039](adr/0039-preprocesamiento-datos.md)) y se serializa en el artefacto, para que la inferencia lo replique idéntico. El store **no** encodea.

> El **target `y_next`** es una columna de **entrenamiento**, no de serving: las filas de serving (mes base más reciente) no lo tienen. El training lo arma por merge de calendario (`periodo + 1 mes`).

## Cómo consumir

### Rol 1 — entrenamiento
El pipeline de `ml/` es la **fuente de las features**; el store es su materialización. Para evitar duplicar lógica, conviene una función compartida `ml.dataset.build_serving_features()` (features por `(pozo, mes)` **sin target/split**) que: (a) use el asset de materialización del store, y (b) la reutilice `build_basic_dataset` (que le agrega target + split). Así training y serving comparten **una sola** definición de features.

### Rol 3 — inferencia (`POST /api/v1/predict`)
Dado `(idpozo, mes_objetivo)`: leer la fila del **mes base** `t` (`periodo = primer día de mes_objetivo − 1 mes`) y pasar **todas las columnas de features** (las 29) al modelo `Production` de MLflow. **No recalcular features.** ⚠️ `feature_reader.py` hoy lee solo 6 columnas viejas (`lag1/lag2/lag3/roll3/antiguedad/tef_lag1`) que **ya no existen**: debe actualizarse a las columnas de arriba. Si no hay fila para ese pozo/mes, devolver el error de contrato definido por Rol 3.

## Versionado y cambios

Cambiar la lista de features del modelo (en `ml/`) es un **cambio de contrato**: se incrementa `feature_set_version`, se actualiza este documento y se re-materializa el store. Como la lista sale del código de `ml/`, el asset de materialización la toma automáticamente; avisar a Rol 3 para que ajuste el reader si cambian las columnas.
