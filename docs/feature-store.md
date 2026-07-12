# Feature Store — contrato (Fase 3)

> **Dueño:** Rol 2 (Feature Store + Orquestación). **Consumidores:** Rol 1 (entrenamiento) y Rol 3 (inferencia).
> Contrato estable del feature store. Decisión y alternativas: [ADR-035](adr/0035-feature-store.md). Diseño del problema: [ADR-028](adr/0028-diseno-problema-modelado.md).

## Por qué existe

La consigna de Fase 3 (RNF) exige que **el procesamiento y la generación de features quede persistido en un feature store** y que **las mismas features sirvan para entrenamiento e inferencia**, para evitar el **training-serving skew**: si el modelo se entrena con features calculadas de una forma y la API las recalcula de otra, las predicciones en producción difieren de las de entrenamiento. La solución: **calcular las features una sola vez** y que training e inferencia **lean lo mismo**.

## Qué es (realineado en Fase 3 — ver Revisión en ADR-035)

Una tabla en el DW (PostgreSQL) que **materializa la salida del pipeline de features de ML** (`ml/features.py` + `ml/dataset.py`), es decir, **exactamente las features que consume el modelo campeón** ([ADR-039](adr/0039-modelo-produccion.md)). La produce un **asset de Dagster** (Rol 2) que ejecuta ese pipeline y escribe la tabla; **no** se reimplementa en SQL/dbt (las features de A —KNN de vecinos, lags por calendario— no se expresan bien en SQL y se desincronizarían).

### Dos modelos → dos tablas (ADR-039)

Desde la Fase 3 se pronostican **dos targets**: petróleo (`prod_pet`) y gas (`prod_gas`), un modelo por target ([ADR-039](adr/0039-modelo-produccion.md)). El store materializa **una tabla por target** (decisión registrada en ADR-035, *Revisión 2*): cada una con **su universo** (pozos con ese target `> 0` en train — el gasífero es más amplio), **sus features de ingeniería** sobre el target (`prod_pet_*` vs `prod_gas_*`) y su `y_next`. Las 5 anclas estáticas son **compartidas** (se duplican entre tablas; trivial al volumen). Rol 3 lee **la tabla del target** que sirve.

| | Petróleo (`prod_pet`) | Gas (`prod_gas`) |
|---|---|---|
| **Tabla** | `features.feat_produccion_pozo_mensual` (nombre histórico) | `features.feat_produccion_pozo_mensual_gas` |
| **Universo** | pozos con `prod_pet > 0` en train | pozos con `prod_gas > 0` en train (más amplio) |
| **Engineered** | `prod_pet_roll3/ratio1/acum12/roll6/acum6/cummax/lag2/delta1` + `well_age_months` | `prod_gas_roll3/ratio1/acum12/roll6/acum6/cummax/lag2/delta1` + `well_age_months` |
| **Experimento / modelo MLflow** | `produccion-forecast` | `produccion-forecast-gas` |

Todo lo demás del contrato es **idéntico** entre las dos tablas (grano, claves, encuadre temporal, categóricas crudas, `y_next` nullable para serving). Donde abajo dice "la tabla", aplica a cada una.

| | |
|---|---|
| **Tablas** | `features.feat_produccion_pozo_mensual` (petróleo) · `features.feat_produccion_pozo_mensual_gas` (gas) |
| **Productor** | el job de retrain (asset `features_refrescadas`) corre el pipeline de `ml/` para los dos targets y materializa ambas tablas (única fuente de verdad de las features) |
| **Grano** | una fila por `(idpozo, mes base t)` |
| **Clave de lookup (inferencia)** | `idpozo` + `periodo` (primer día del mes base `t`) |
| **Consumidores** | Rol 1 (training, mismas features), Rol 3 (`/forecast`, por target) y el precómputo del forecast (ADR-043) |
| **Refresh** | lo materializa el **job de retrain** antes de entrenar (ADR-040). **No** es parte del refresh del DW (`dw_publish`), para no acoplar el pipeline de datos a las deps de `ml/`. |

## Encuadre temporal (fila = mes base `t`)

Cada fila es el **mes base `t`**: todas las features están disponibles al cierre de `t` (anti-leakage) y sirven para predecir el **target** (`prod_pet` o `prod_gas`) del **mes siguiente `t+1`**. Para inferir `t+1`, la API lee la fila del mes base `t = (t+1) − 1 mes` **en la tabla del target pedido**.

> El lookup de inferencia usa siempre `periodo` (mes base). El feature `mes` (ADR-041) se define como el mes del **período objetivo** (`t+1`, `ml/dataset.py`), conocido de antemano (no es leakage); el motor de forecast lo setea igual en cada paso (`ml/forecast.py`), así que no hay training-serving skew.

## Columnas (contrato)

El store materializa el **set de ganancia positiva de la selección** ([ADR-041](adr/0041-seleccion-features-forecast.md)): las features con *permutation importance* en val `> 0` que entrena el modelo. La **fuente de verdad** es el código de ML (`ml.features.selected_features(target)` — la misma lista que usa `ml/train.py`, así store y modelo no pueden divergir). Los sets **difieren por target**: **27 features en petróleo, 19 en gas** (rankearon distinto), así que las tablas tienen **distinto ancho**: **31 columnas** en petróleo (3 claves + 27 features + `y_next`) y **23** en gas (3 + 19 + 1). La **lista completa con la importancia de cada feature** está en el [ADR-041](adr/0041-seleccion-features-forecast.md); en estructura, cada tabla combina:

- **Claves / lookup:** `idpozo` (bigint), `periodo` (date, mes base), `periodo_objetivo` (date, `t+1`).
- **Núcleo autorregresivo** (prefijo `prod_pet_` / `prod_gas_` según el target): nivel del mes `t` + medias móviles, ratios/deltas, acumulados, `cummax`, lags, `std3`, etc. (recalculables → habilitan el forecast recursivo).
- **Estáticas / categóricas del pozo:** en petróleo `areayacimiento`, `profundidad`, `coordenadax/y`, `well_age_months` + `tipopozo`, `empresa`, `areapermisoconcesion`, `proyecto`, `cuenca`, `tipoextraccion`, `mes`; en gas la estática se reduce a `tipoestado` + categóricas (`clasificacion`, `sub_tipo_recurso`, `provincia`, `formprod`, `tipoextraccion`) + `mes` (asimetría documentada en ADR-041).
- **Target:** `y_next` (float, nullable).

Las features de importancia ≈ 0 / negativa del ranking (ADR-041) **no se persisten**: si un reentreno futuro re-incorpora alguna, se agrega a `selected_features` y el retrain re-materializa solo (ver *Versionado*).

> El store **no** materializa columnas de metadata (`feature_set_version` / `computed_at`): el versionado del contrato se lleva por este documento + git (ver *Versionado*), no por columnas en la tabla.

> Las categóricas (`areayacimiento`, `tipopozo`, `empresa`, `tipoestado`, …) van **crudas**: el one-hot/imputación/escalado vive en el `Pipeline` del modelo (se ajusta solo en train, [ADR-038](adr/0038-preprocesamiento-datos.md)) y se serializa en el artefacto, para que la inferencia lo replique idéntico. El store **no** encodea.

> El **target `y_next`** es una columna de **entrenamiento**, no de serving: las filas de serving (mes base más reciente) no lo tienen. El training lo arma por merge de calendario (`periodo + 1 mes`).

## Cómo consumir

### Rol 1 — entrenamiento
El store **materializa el pipeline de `ml/`** (el asset `features_refrescadas` reusa `ml.features.add_engineered_features(target=...)` + las listas `ml.dataset.BASIC_*`), con **paridad validada** contra `build_basic_dataset` para **los dos targets** (0 diferencias). El training puede seguir usando `build_basic_dataset(target=...)` (mismas features) o leer la tabla del target (filas con `y_next` no nulo). Como la lista de features sale del código de `ml/`, cambiarla ahí re-materializa ambas tablas sin reescribir nada. `ml/requirements.txt` ya existe (Rol 1) → el venv del daemon instala las deps de `ml/`.

### Rol 3 — inferencia (`GET /api/v1/forecast`, recursivo)
La inferencia se unificó en `/forecast` (recursivo mensual, ADR-042); el paso unitario de un mes quedó subsumido, sin endpoint propio. El forecast lee del store (`feature_reader.get_history_for_forecast`, **implementado**): (1) la **fila del mes base** (último mes del pozo, con `y_next` NULL — el store la conserva vía left-join) → predice t+1 **sin recalcular**; (2) la **serie** `(periodo, <target>)` de todos los meses → recalcula los meses futuros (t+2+). Elige la **tabla del target** (`feat_produccion_pozo_mensual` / `..._gas`) y descarta `idpozo/periodo/periodo_objetivo/y_next` (guarda anti-leak). **Dependencia operativa:** el store debe estar **re-materializado** con el set de la selección (ADR-041) — lo hace el retrain (ADR-040) reusando `ml.features`, así que la paridad training-serving está garantizada. Además, `/forecast` sirve primero el **precómputo** (abajo) cuando está fresco.

## Pronóstico precomputado (ADR-043)

Además del store, el job de retrain deja el **pronóstico de 12 meses por pozo ya
calculado**, para que `/forecast` responda como lookup (sin modelo ni MLflow en el
request). Misma convención de tablas que el store:

| | |
|---|---|
| **Tablas** | `features.pred_produccion_pozo_mensual` (petróleo) · `features.pred_produccion_pozo_mensual_gas` (gas) |
| **Productor** | asset `forecast_precomputado` del job de retrain (corre después de reentrenar/promover, ADR-040/043) |
| **Grano** | una fila por `(idpozo, mes pronosticado)` — 12 filas por pozo, desde `ultimo_observado + 1` |
| **Columnas** | `idpozo`, `periodo` (mes pronosticado), `prediccion` (float), `ultimo_observado` (último mes con dato real del pozo al generar), `model_name`, `model_version`, `generado_en` |
| **Consumidor** | la API (`feature_reader.get_precomputed_forecast`): sirve estas filas **solo si están frescas** (`ultimo_observado` == `max(periodo)` del store para ese pozo); si no, recalcula on-the-fly con el motor recursivo |
| **Generación** | mismo motor (`ml.forecast.recursive_forecast`) + mismo modelo **Production** + mismas features del store que usaría el on-the-fly ⇒ **mismos valores** (transparente para el usuario) |

> Si un target no tiene modelo en Production, el asset lo **saltea** (no escribe su tabla)
> y la API sigue sirviendo por el camino on-the-fly. La tabla no se usa para decidir
> 404/422: esas validaciones salen del mismo `ultimo_observado` en ambos caminos.

## Versionado y cambios

Cambiar la lista de features del modelo (en `ml/`) es un **cambio de contrato**: se documenta acá (sección *Columnas*, con su fecha/commit) y se re-materializa el store. Como la lista sale del código de `ml/`, el asset de materialización la toma automáticamente para los dos targets; avisar a Rol 3 para que ajuste el reader si cambian las columnas. Un **target nuevo** (otra producción) = una tabla nueva (`table_for(target)` en `feature_store_build.py`) + sumarlo a `ml.config.TARGETS`.
