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
2. **Encuadre "fila = mes base `t`"** (alineado con `ml/dataset.py`, `y_next = shift(-1)`): coherente con `POST /api/v1/predict` y con `/forecast`.
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
- Un **asset de Dagster** (Rol 2) ejecuta el pipeline de features de ML y escribe `features.feat_produccion_pozo_mensual` con las columnas que consume el modelo.
- **Una sola definición de features** vive en `ml/` (idealmente una función `build_serving_features()` que reusan tanto el training como la materialización del store) → cero skew por construcción.
- Las categóricas siguen **crudas** (encoding en el `Pipeline` del modelo, ADR-039).
- La API (Rol 3) lee las **mismas** columnas del store.

**Consecuencia sobre la Alternativa A original.** Se descarta reproducir las features en dbt SQL (quedaba elegida cuando eran simples). El modelo dbt `feat_produccion_pozo_mensual` se reemplaza por la materialización Python. Se pierde el linaje dbt→DataHub de la tabla de features (aceptable: la lógica vive en `ml/` y el linaje del modelo lo lleva MLflow); se gana **identidad garantizada** entre las features de training e inferencia. Contrato detallado: [docs/feature-store.md](../feature-store.md).
