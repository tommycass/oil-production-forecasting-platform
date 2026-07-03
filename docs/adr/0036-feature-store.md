# Título: ADR-036: Feature store para el modelo predictivo

**Estado:** Propuesta

> Decide **cómo y dónde se persisten las features** del forecast de producción. Complementa [ADR-028](0028-diseno-problema-modelado.md) (diseño del problema: target `prod_pet` t+1, universo petrolero, features autoregresivas) y reutiliza el stack de la Fase 2 ([ADR-014](0014-arquitectura-medallion.md) Medallion, [ADR-024](0024-motor-transformacion-y-dw.md) dbt+Postgres, [ADR-017](0017-plataforma-gobierno-datos.md) DataHub). Contrato publicado: [docs/feature-store.md](../feature-store.md).

## Contexto

La Fase 3 exige (RNF) que **el procesamiento y la generación de features quede persistido en un feature store** y que **las mismas features sirvan para entrenamiento e inferencia**, para evitar el **training-serving skew** (que la API recalcule features distinto a como se entrenaron).

Hoy las features se calculan en `ml/dataset.py` (pandas) leyendo un CSV provisorio. Eso no es un feature store: no está persistido, no es consumible por la API y duplicaría la lógica si la API recalcula. Hay que decidir la tecnología del store.

El contexto del proyecto: ya existe un DW PostgreSQL con capa Gold modelada en estrella, transformaciones en **dbt**, orquestación en **Dagster** y linaje en **DataHub** (Fase 2). El volumen es moderado (~400 k filas de producción) y **la entrega es sin servicio live en producción**, por lo que no hay un requisito de serving online de baja latencia.

### Alternativas evaluadas

#### Alternativa A — Tabla en el Postgres existente, vía dbt (seleccionada)

Un modelo dbt (`features.feat_produccion_pozo_mensual`) materializado como **tabla** en un esquema `features`, que deriva de Gold (`fact_produccion_mensual` + dims) y calcula las features con window functions.

- **Ventajas:** sin infraestructura nueva (es una tabla SQL más); reusa dbt (versionado, repetible, no manual), sus **tests de Data Quality** y el **linaje en DataHub** (Bronze→Silver→Gold→Features) sin configuración extra; la API y el training la consumen con un simple `SELECT`; mismo workflow que el Analytics Engineer ya conoce. Es exactamente el "feature store offline = tabla en el Postgres existente" que sugiere la consigna.
- **Desventajas:** no ofrece serving **online** de baja latencia ni point-in-time joins automáticos; si el caso de uso creciera a inferencia online masiva, habría que evaluar una herramienta dedicada.

#### Alternativa B — Feast (feature store dedicado)

Feast gestiona un offline store (p. ej. Postgres) y un online store (p. ej. Redis), con definiciones de features versionadas y materialización a ambos.

- **Ventajas:** estándar de la industria; resuelve point-in-time correctness y serving online; separa la definición de features de su almacenamiento.
- **Desventajas:** agrega un **servicio y un modelo conceptual nuevos** (entities, feature views, registry, materialización online↔offline) que hay que desplegar y operar; el online store (Redis) no aporta nada acá porque **no hay serving live**; duplicaría el rol que dbt+DataHub ya cumplen para versionado y linaje. **Exceso de ingeniería** para el volumen y los requisitos actuales (mismo criterio que [ADR-027](0027-semantic-layer.md) al descartar Cube.dev).

#### Alternativa C — Asset de Dagster en Python que escribe la tabla

Un asset de Dagster que calcula las features en pandas (reusando `ml/dataset.py`) y las escribe a Postgres con `to_sql`.

- **Ventajas:** reusa la lógica de features ya escrita por Rol 1 tal cual; un solo lenguaje (Python).
- **Desventajas:** la tabla quedaría **fuera del proyecto dbt** → sin tests de DQ ni linaje automático en DataHub; el cálculo en pandas sobre ~400 k filas es menos eficiente y menos declarativo que SQL en el motor; y mantiene la lógica de features en imperativo en vez de un modelo versionado. Pierde las ventajas que la Fase 2 ya construyó.

## Decisión

**Alternativa A: tabla en Postgres vía dbt.** El feature store es el modelo `features.feat_produccion_pozo_mensual`, materializado como tabla en el esquema `features`, derivado de Gold.

Decisiones de diseño asociadas:

1. **Una sola definición de features (anti-skew).** El modelo dbt reproduce **exacto** las features de `ml/dataset.py` (`lag1/2/3`, `roll3` con min_periods=3, `antiguedad` 0-based, `tef_lag1`, target `y_next`). `ml/dataset.py` se refactoriza para **leer del store** en vez de recalcular, y la API lee del **mismo** store. Así la feature se calcula **una vez** y es idéntica en training e inferencia.
2. **Encuadre "fila = mes base `t`"** (alineado con `ml/dataset.py`, `y_next = shift(-1)`): coherente con la inferencia de `/forecast` (ADR-044).
3. **Categóricas crudas en el store; encoding en el modelo.** El store guarda `tipopozo`, `cuenca`, etc. sin encodear; el one-hot/scaling vive en el pipeline del modelo y se serializa en MLflow, para que la inferencia lo replique. Evita acoplar el store a un algoritmo.
4. **Deriva de Gold, no de Silver/CSV.** Mantiene la arquitectura Medallion y el linaje gobernado.

## Consecuencias

**Positivas:**
- Cumple el RNF (features persistidas, mismas para training e inferencia) **sin infraestructura nueva**.
- Hereda tests de DQ y linaje en DataHub; el store es versionado, repetible y no manual.
- Contrato estable y simple de consumir (`SELECT`) para Rol 1 y Rol 3.

**Negativas / trade-offs:**
- No hay serving online de baja latencia ni point-in-time joins automáticos (no requeridos hoy; la entrega es sin servicio live). Si el proyecto escalara a inferencia online, se reevaluaría Feast (Alternativa B).
- La definición de features queda en SQL (dbt) y debe mantenerse **sincronizada** con cualquier feature nueva que Rol 1 quiera; se gestiona vía `feature_set_version` y el contrato publicado.

## Relación con otros ADRs

- **ADR-028:** define target, universo y features; este ADR las materializa y persiste.
- **ADR-024 / ADR-014:** reusa dbt+Postgres y la capa Gold del Medallion.
- **ADR-017 / ADR-027:** el linaje hasta `features.*` se ingiere en DataHub igual que `semantic.*`; mismo criterio anti-sobreingeniería.
- **ADR-041 (orquestación del retrain):** el job de retrain materializa este store antes de entrenar.

---

## Revisión (Fase 3) — materialización del pipeline de ML

**Contexto del cambio.** La decisión original (Alternativa A: reproducir las features en dbt SQL) se tomó cuando las features eran 6 autoregresivas simples (`lag1/2/3`, `roll3`, `antiguedad`, `tef_lag1`) que calzaban en SQL. Al integrar el trabajo de modelado (Rol 1), el modelo campeón (ADR-040) pasó a usar **~29 features** que incluyen ingeniería **en Python**: medias/lags **por calendario** (`prod_pet_roll3/delta1/lag12/acum6`), `water_cut`, y `prod_vecinos_mean` (media de los k pozos vecinos por **KNN** sobre coordenadas). Reproducir eso en SQL es impráctico (KNN) y, sobre todo, **se desincronizaría** de `ml/features.py` (reintroduciendo el training-serving skew que el store debe evitar). Además se detectó que el training calculaba features en pandas sin pasar por el store y la inferencia leía columnas viejas → **desalineación de tres puntas**.

**Decisión revisada.** El feature store **materializa la salida del pipeline de features de `ml/`** (única fuente de verdad), en vez de reimplementarlo en dbt:
- Un **asset de Dagster** (Rol 2) ejecuta el pipeline de features de ML (importa `ml.features.add_engineered_features` + las listas `ml.dataset.BASIC_*`) y escribe `features.feat_produccion_pozo_mensual` con las columnas que consume el modelo, leyendo de `bronze.produccion`. Se materializa **dentro del job de retrain** (`features_refrescadas`, ADR-041), **no** en `dw_publish`, para no acoplar el refresh del DW (Fase 2) a las deps de `ml/`.
- **Una sola definición de features** vive en `ml/` y la materialización la **reusa por import** (no la reimplementa) → cero skew por construcción. Validado: paridad exacta contra `build_basic_dataset` (0 diferencias).
- Las categóricas siguen **crudas** (encoding en el `Pipeline` del modelo, ADR-039).
- La API (Rol 3) lee las **mismas** columnas del store.

**Consecuencia sobre la Alternativa A original.** Se descarta reproducir las features en dbt SQL (quedaba elegida cuando eran simples). El modelo dbt `feat_produccion_pozo_mensual` se reemplaza por la materialización Python. Se pierde el linaje dbt→DataHub de la tabla de features (aceptable: la lógica vive en `ml/` y el linaje del modelo lo lleva MLflow); se gana **identidad garantizada** entre las features de training e inferencia. Contrato detallado: [docs/feature-store.md](../feature-store.md).

---

## Revisión 2 (Fase 3) — dos targets (petróleo + gas)

**Contexto del cambio.** La cátedra confirmó que se pronostican **ambas** producciones, petróleo (`prod_pet`) y gas (`prod_gas`), con **un modelo por target** ([ADR-042](0042-modelo-prediccion-gas.md)). El pipeline de `ml/` se parametrizó por `target`: cambian el **universo** (pozos con ese target `> 0` en train; el gasífero es más amplio), las **features de ingeniería autorregresivas** (`prod_pet_*` vs `prod_gas_*`) y el `y_next`. Las 8 numéricas base y las 14 categóricas son **compartidas**. Hay que decidir **cómo se persiste el segundo target** en el store.

### Alternativas evaluadas (layout del store)

- **a) Una tabla por target (elegida).** `feat_produccion_pozo_mensual` (petróleo, nombre histórico) y `feat_produccion_pozo_mensual_gas` (gas), cada una con sus 29 features + `y_next` y su universo train-only.
  - **Ventajas:** el universo train-only de cada target queda **limpio y aislado** (sin filas de un target con `y_next` nulo del otro); cada modelo —training e inferencia— hace un `SELECT *` de **su** tabla sin lógica de selección de columnas; reusa `build_store_features(df, target=...)` dos veces (cero código nuevo de features); la tabla de petróleo **no cambia de nombre** (no rompe consumidores existentes); paridad validable por target. Es la recomendación de Rol 1 en el handoff.
  - **Desventajas:** **duplica** las 22 columnas compartidas (8 numéricas + 14 categóricas) entre tablas. Trivial al volumen (~6 k filas por tabla) y sin costo de mantenimiento (la lista sale del mismo código de `ml/`).
- **b) Una tabla "ancha".** Las compartidas una sola vez + **ambos** bloques de ingeniería (`prod_pet_*` y `prod_gas_*`) + dos `y_next`, sobre la **unión** de universos.
  - **Ventajas:** sin duplicar las compartidas.
  - **Desventajas:** cada modelo debe **seleccionar sus columnas**; las filas fuera del universo de un target quedan con su `y_next` nulo y features de ingeniería del otro target potencialmente sin sentido → mezcla dos universos en una tabla y complica la paridad y el reader de inferencia.
- **c) Columna `target` / partición.** Una tabla con una columna discriminante y engineered de nombre genérico; filas duplicadas por `(idpozo, periodo, target)`.
  - **Desventajas:** duplica **todas** las filas (no solo las columnas compartidas), mezcla universos y obliga a `WHERE target = …` en cada lectura; pierde el `SELECT *` simple.

### Decisión revisada

**Alternativa (a): una tabla por target.** `build_store_features(df, target)` se parametriza por target y `materializar_todos` recorre `ml.config.TARGETS` escribiendo `table_for(target)` (petróleo mantiene el nombre histórico; gas lleva sufijo `_gas`, igual que el experimento/modelo de MLflow). El job de retrain ([ADR-041](0041-orquestacion-retrain.md)) materializa las dos tablas y reentrena los dos modelos. Rol 3 lee la tabla del target que sirve.

**Consecuencia.** El contrato ([docs/feature-store.md](../feature-store.md)) pasa a describir dos tablas idénticas en estructura salvo el nombre de las engineered y el universo. Paridad re-validada contra `build_basic_dataset(target=...)` para **ambos** targets (0 diferencias). Un target nuevo en el futuro = una tabla nueva (sumarlo a `TARGETS`), sin tocar la lógica de features.
