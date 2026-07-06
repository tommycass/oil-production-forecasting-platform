# Título: ADR-035: Feature store para el modelo predictivo

**Estado:** Aceptada

> Decide **cómo y dónde se persisten las features** del forecast de producción, y con qué **layout** para los dos targets (petróleo y gas, ADR-039). Complementa [ADR-028](0028-diseno-problema-modelado.md) (diseño del problema) y [ADR-033](0033-feature-engineering.md)/[ADR-041](0041-seleccion-features-forecast.md) (features y selección). Contrato publicado: [docs/feature-store.md](../feature-store.md).

## Contexto

La Fase 3 exige (RNF) que **la generación de features quede persistida en un feature store** y que **las mismas features sirvan para entrenamiento e inferencia**, para evitar el **training-serving skew** (que la API recalcule features distinto a como se entrenaron).

Las features del modelo son **ingeniería en Python** (`ml/features.py`): medias/lags **por calendario**, ratios y acumulados autorregresivos del target (`{target}_roll3/ratio1/acum12/std3/…`), más anclas estáticas y categóricas del pozo (ADR-033/041). Son las que habilitan el forecast **recursivo** (ADR-042) y varias son impracticables en SQL (p. ej. el KNN de vecinos que se probó y descartó). El contexto del proyecto: ya existe un DW PostgreSQL (Fase 2) con Dagster orquestando y DataHub para linaje; el volumen es moderado (~6 k filas por tabla de features) y **la entrega es sin servicio live**, así que no hay requisito de serving online de baja latencia.

### Alternativas evaluadas

#### Tecnología del store

- **Materialización en Postgres del pipeline de `ml/`, vía asset de Dagster (elegida).** Un asset (Rol 2) **importa y ejecuta** `ml.features` sobre el crudo de `bronze.produccion` y escribe la tabla en un esquema `features`. La lógica de features **no se reimplementa**: se reusa por import, así la feature se calcula **una vez** y es idéntica en training e inferencia (cero skew por construcción).
- **Modelo dbt en SQL (descartada).** Reproducir las features en SQL con window functions calzaba cuando eran lags simples, pero las autorregresivas por calendario y el KNN de vecinos son **impracticables/impagables en SQL** y, sobre todo, **se desincronizarían** de `ml/features.py` — reintroduciendo el training-serving skew que el store debe evitar. Se pierde el linaje dbt→DataHub de la tabla de features (aceptable: la lógica vive en `ml/` y el modelo se versiona en MLflow).
- **Feast (feature store dedicado, descartada).** Estándar de industria (offline + online store, point-in-time), pero agrega un servicio y un modelo conceptual nuevos que hay que operar; el online store (Redis) **no aporta** porque no hay serving live. **Exceso de ingeniería** para el volumen y los requisitos actuales (mismo criterio que ADR-027 con Cube.dev).

#### Layout para dos targets (petróleo + gas, ADR-039)

- **Una tabla por target (elegida).** `feat_produccion_pozo_mensual` (petróleo) y `feat_produccion_pozo_mensual_gas` (gas), cada una con sus features (ADR-041) + `y_next` y su **universo train-only**. El universo de cada target queda **limpio y aislado**; cada modelo hace un `SELECT *` de **su** tabla sin lógica de selección de columnas; reusa `build_store_features(df, target)` dos veces (cero código nuevo). Duplica las columnas compartidas entre tablas, pero es trivial al volumen.
- **Una tabla "ancha" (descartada).** Compartidas una vez + ambos bloques de ingeniería + dos `y_next`: obliga a cada modelo a seleccionar sus columnas y **mezcla dos universos** (filas con `y_next` nulo del otro target), complicando paridad y reader.
- **Columna `target` / partición (descartada).** Duplica **todas** las filas, mezcla universos y obliga a `WHERE target = …` en cada lectura; pierde el `SELECT *` simple.

## Decisión

**El feature store es la materialización en Postgres de la salida del pipeline de features de `ml/`** (fuente única de verdad), con **una tabla por target** en el esquema `features`:

1. **Una sola definición de features, reusada por import (anti-skew).** El asset `features_refrescadas` (Rol 2) ejecuta `ml.features.add_engineered_features(target=…)` + las columnas base de `ml.dataset` sobre `bronze.produccion`, y persiste **exactamente** las columnas que consume el modelo (`ml.features.selected_features(target)` + claves + `y_next`). Training e inferencia leen las **mismas** columnas. Paridad validada contra `build_basic_dataset(target=…)` para ambos targets (0 diferencias).
2. **Una tabla por target.** `materializar_todos` recorre `ml.config.TARGETS` y escribe `table_for(target)` (petróleo mantiene el nombre histórico; gas lleva sufijo `_gas`, igual que su experimento/modelo en MLflow). Un target nuevo = una tabla nueva (sumarlo a `TARGETS`), sin tocar la lógica de features.
3. **Encuadre "fila = mes base `t`"** (`y_next` = target de `t+1` por merge de calendario): coherente con la inferencia de `/forecast` (ADR-042). La fila del último mes se conserva con `y_next` NULL (left-join) para que la API infiera el mes siguiente.
4. **Categóricas crudas en el store; encoding en el modelo.** El store guarda `tipopozo`, `empresa`, etc. sin encodear; el one-hot/imputación/escalado vive en el `Pipeline` del modelo y se serializa en MLflow (ADR-038), para que la inferencia lo replique.
5. **Se materializa dentro del job de retrain** (`features_refrescadas`, ADR-040), **no** en `dw_publish`, para no acoplar el refresh del DW (Fase 2) a las dependencias de `ml/`. Es el único lugar donde se materializa el store.

## Consecuencias

**Positivas:**
- Cumple el RNF (features persistidas, idénticas en training e inferencia) **sin infraestructura nueva** y con **cero skew por construcción** (reuse por import, no reimplementación).
- Contrato estable y simple de consumir (`SELECT *` de la tabla del target) para Rol 1 y Rol 3.
- Cambiar el set de features es cambiar `ml.features.selected_features`: el retrain re-materializa ambas tablas solo, sin reescribir SQL.

**Negativas / trade-offs:**
- La tabla de features queda **fuera del proyecto dbt** → sin tests de DQ ni linaje automático en DataHub para ella (aceptable: la lógica vive en `ml/` y el modelo se versiona en MLflow; el linaje Bronze→Silver→Gold del DW se mantiene).
- No hay serving online de baja latencia ni point-in-time joins automáticos (no requeridos; entrega sin servicio live). Si escalara a inferencia online masiva, se reevaluaría Feast.
- Duplica las columnas compartidas entre las dos tablas (trivial al volumen).

## Relación con otros ADRs

- **ADR-028 / ADR-033 / ADR-041:** definen target, universo, features y selección; este ADR las materializa y persiste.
- **ADR-039:** dos targets → dos tablas (una por producción).
- **ADR-040 (orquestación del retrain):** el job materializa este store (`features_refrescadas`) antes de entrenar.
- **ADR-042 / ADR-043:** el serving lee del store (mes base + serie) para el forecast recursivo y el precómputo.
- **ADR-038:** encoding/imputación en el `Pipeline` del modelo (categóricas crudas en el store).
