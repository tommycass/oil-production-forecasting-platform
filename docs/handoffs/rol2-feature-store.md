# Handoff Rol 1 → Rol 2: Feature store del forecast de producción

**De:** Rol 1 (ML Engineer) · **Para:** Rol 2 (Feature store + orquestación)
**Objetivo:** que materialices en el feature store **exactamente las mismas features** que usa el entrenamiento, calculadas de la misma forma, para evitar *training-serving skew*.

Todo lo de acá está implementado y validado en `ml/` y documentado en los ADR-028, 031, 032, 033, 036, 037.

> **Novedad — son DOS modelos:** además de petróleo (`prod_pet`) ahora hay un modelo de
> **gas** (`prod_gas`), ADR-042. Comparten casi todas las features; cambian la **columna
> target**, el **universo** (pozos con ese target > 0 en train) y las **features de
> ingeniería autorregresivas** (se calculan sobre el target). El pipeline de `ml/` está
> **parametrizado por `target`**: `build_basic_dataset(target="prod_gas")`. Ver §2.2 y §9.

---

## 1. Qué predicen los modelos (contexto mínimo)

- **Targets:** `prod_pet` (petróleo) y `prod_gas` (gas), del **mes siguiente** (`y_next` = producción en `t+1`). Un modelo por target.
- **Grano:** una fila por **(pozo, mes)** → claves `idpozo`, `periodo` (primer día del mes).
- **Cada fila:** las features son del **mes `t`**; el target es la producción de `t+1`.
- **Métrica:** RMSE / R² (ADR-028).

---

## 2. Features a materializar (29) — origen y cálculo

Fuente provisoria hoy: `data/_explore/produccion_full.csv`. En el store deben salir de **Gold**: las **medidas mensuales** de `fact_produccion_mensual` y los **atributos del pozo** de `dim_pozo` (confirmá el linaje exacto de cada columna contra Gold).

### 2.1 Numéricas base (8) — directas del mes `t`

| Feature | Origen (raw) | Tipo | Nota |
|---|---|---|---|
| `prod_pet` | medida mensual | m³ | producción de petróleo del mes t (= "mes anterior" respecto del target) |
| `prod_gas` | medida mensual | m³/dam³ | |
| `prod_agua` | medida mensual | m³ | |
| `tef` | medida mensual | horas/días efectivos | tiempo efectivo de producción |
| `profundidad` | atributo pozo | m | estático |
| `coordenadax` | atributo pozo | coord | estático |
| `coordenaday` | atributo pozo | coord | estático |
| `mes` | calendario | 1–12 | **mes del MES OBJETIVO (t+1)**, no del mes de las medidas (ver §3) |

> `anio` **no** es feature (se excluye por extrapolación, ADR-031). Se usa solo para construir `periodo`.

### 2.2 Features de ingeniería (7) — derivadas, ver `ml/features.py`

Todas se calculan **por pozo**, con **lags por calendario** (no `shift` de filas). Insumos: el **target** (`prod_pet` o `prod_gas`), `prod_agua` (mensual) y `coordenadax/coordenaday` (estáticos). Las autorregresivas (roll3/delta1/lag12/acum6, vecinos y la actividad) se calculan **sobre la columna target** del modelo: en el de petróleo sobre `prod_pet`, en el de gas sobre `prod_gas` (nombres `{target}_roll3`, etc.). `water_cut` es físico (agua/petróleo) e igual para los dos.

| Feature (petróleo / gas) | Cálculo (sobre la serie del pozo, `m` = target del modelo) |
|---|---|
| `prod_pet_roll3` / `prod_gas_roll3` | media de `m` en {t, t-1, t-2} |
| `prod_pet_delta1` / `prod_gas_delta1` | `m(t) − m(t-1)` |
| `prod_pet_lag12` / `prod_gas_lag12` | `m(t-12)` (mismo mes del año anterior) |
| `prod_pet_acum6` / `prod_gas_acum6` | suma de `m` en {t … t-5} |
| `water_cut` | `prod_agua / (prod_agua + prod_pet)` en t; `0` si el denominador es 0 (igual en ambos) |
| `produjo_mes_pasado` | `1` si `m(t) > 0`, si no `0` |
| `prod_vecinos_mean` | media de `m(t)` de los **5 pozos más cercanos** por (`coordenadax`,`coordenaday`), excluido el propio pozo |

### 2.3 Categóricas (14) — atributos, valor en el mes `t`

`tipoextraccion`, `tipoestado`, `tipopozo`, `empresa`, `formprod`, `formacion`, `areapermisoconcesion`, `areayacimiento`, `cuenca`, `provincia`, `proyecto`, `clasificacion`, `subclasificacion`, `sub_tipo_recurso`.

Se guardan **como texto crudo** (sin codificar). El one-hot **no** va en el store: vive en el `Pipeline` del modelo (ver §5).

### 2.4 Target

`y_next` = el **target** (`prod_pet` o `prod_gas`) del mes `t+1` (para entrenar). En **inferencia** no se materializa (es lo que se predice). Cada modelo tiene su propio `y_next`.

---

## 3. Reglas anti-leakage que el store DEBE respetar

Son la parte crítica. Si se rompen, el modelo entrena con información que no tendría en producción.

1. **Features solo de `t` o anterior.** Ninguna feature puede usar datos de `t+1` (el mes que se predice). Las medidas son de `t`; los lags/ventanas, de `t` hacia atrás.
2. **Lags por calendario, no por fila.** `t-k` se busca por `periodo - k meses` (no tomar "la fila anterior"); si falta ese mes, queda `NaN`. Replicar `ml.features._calendar_lag`. Esto evita que un hueco en la serie corra el lag.
3. **`mes` es el mes del target (`t+1`)**, no el de las medidas. Es la única excepción "del futuro" y es válida porque la fecha a predecir se conoce de antemano (determinística, no es leakage).
4. **Target por merge de calendario** (`periodo + 1 mes`), no por `shift(-1)`: garantiza que `y_next` sea siempre exactamente el mes siguiente.
5. **Universo train-only, por target.** El conjunto de pozos = los que tienen **el target** (`prod_pet` para el modelo de petróleo, `prod_gas` para el de gas) `> 0` en algún mes **≤ TRAIN_END** (`2023-07-01`). No definir el universo con todo el histórico (metería pozos que recién producen en val/test → leakage de selección). El universo gasífero es distinto (y más grande) que el petrolero.
6. **Descartar producción negativa.** Filas con `prod_pet`/`prod_gas`/`prod_agua < 0` son errores de dato → se eliminan (ADR-039).

---

## 4. Esquema / contrato sugerido del store

| Columna | Tipo | Rol |
|---|---|---|
| `idpozo` | int | clave |
| `periodo` | date (1° del mes) | clave (mes `t` de las features) |
| 8 numéricas base | float (`mes` int) | feature |
| 7 de ingeniería | float (`produjo_mes_pasado` 0/1) | feature |
| 14 categóricas | text | feature |
| `y_next` | float, nullable | target (solo para training) |

- **Una fila por (`idpozo`, `periodo`)**. `periodo` = mes de las features.
- Las features de historia (`*_lag12`, `*_acum6`, `roll3`, `delta1`, vecinos) son **`NaN`** en los primeros meses de cada pozo: **dejalas `NaN`** (la imputación la hace el modelo, §5).
- Mantené `periodo_objetivo` (= `periodo + 1 mes`) si te sirve para joins, pero no es feature.

---

## 5. Qué **NO** va en el store (es del modelo)

El **preprocesamiento** vive dentro del `Pipeline` del modelo (`ml/preprocessing.py`) y viaja con el modelo serializado, así se aplica idéntico en train e inferencia:

- **Imputación de NaN** (0+flag en volúmenes/variación, mediana en físicas/operativa, `DESCONOCIDO` en categóricas).
- **One-hot** de las categóricas (con fallback `DESCONOCIDO`).
- (No hay clipping de outliers ni transformación del target — decidido con evidencia, ADR-039.)

→ El store materializa las **features crudas** (las 29 + `y_next`). El modelo se encarga del resto. Así, si Rol 3 sirve el modelo, solo tiene que leer estas mismas features del store y pasárselas al `Pipeline`.

---

## 6. Split temporal (para que entrenamiento y reproceso coincidan)

- `train`: `periodo <= 2023-07-01`
- `val`: `2023-07-01 < periodo <= 2024-11-01`
- `test`: `periodo > 2024-11-01`

(`ml/config.py`: `TRAIN_END`, `VAL_END`.)

**Reproceso por fecha (`RETRAIN_ASOF`) — ya honrado por `ml/`.** Para el retrain parametrizable por fecha (tu tarea 2.3), el corte se hace por **`RETRAIN_ASOF`** (la env var que ya pasás desde el job): `ml.config.retrain_asof()` la lee y `build_basic_dataset(..., asof=...)` **recorta `periodo <= asof` antes** de definir el universo y las features. Así, al reprocesar una fecha pasada, el universo y los lags se recalculan **solo con datos ≤ asof** (no usa datos posteriores → anti-leakage del backfill). **No** movemos `TRAIN_END`/`VAL_END`: el split fijo del ADR-028 se mantiene y los períodos que aún no existen a esa fecha (p. ej. `test` si `asof < VAL_END`) simplemente se omiten en la evaluación. Si tu materialización del store también quiere respetar `asof` para un backfill, aplicá el mismo recorte `periodo <= asof` sobre el crudo antes de construir las features.

---

## 7. Código de referencia (fuente de verdad)

- `ml/features.py` — las 7 features de ingeniería (replicá esta lógica en el store).
- `ml/dataset.py` — `build_basic_dataset(target=...)` (universo, drop de negativos, merge del target, split) y las listas `BASIC_NUMERIC_FEATURES` / `BASIC_CATEGORICAL_FEATURES`. `features.engineered_feature_names(target)` da los nombres de las 7 de ingeniería para cada target.
- `ml/config.py` — `TARGET` (default), `TARGETS` (`prod_pet`, `prod_gas`), `TRAIN_END`, `VAL_END`.
- ADR-031 (dataset anti-leakage), ADR-033 (features), ADR-039 (preprocesamiento).

## 8. Cómo validar paridad store ↔ training

Generá las features con tu pipeline para un set de `(idpozo, periodo)` y comparalas contra las de `build_basic_dataset` (mismas columnas, mismos valores). Diferencias = skew a corregir antes de servir.

---

## 9. Decisión conjunta pendiente

1. **Esquema final del store** (nombres/tipos exactos y el linaje a Gold de cada columna), que es lo que desbloquea también a Rol 3 para la inferencia.
2. **Cómo materializar los DOS modelos.** Las 8 numéricas base + 14 categóricas son **compartidas**; lo que difiere entre petróleo y gas son las **7 de ingeniería** (`prod_pet_*` vs `prod_gas_*`), el **universo** y el `y_next`. Opciones a acordar:
   - **a) Una tabla por target** (`feat_produccion_pozo_mensual` y `feat_..._gas`), cada una con sus 29 features + `y_next`, su universo. Más simple de servir (cada modelo lee su tabla), algo de duplicación de las columnas compartidas.
   - **b) Una tabla "ancha"** con las compartidas + **ambos** bloques de ingeniería (`prod_pet_*` y `prod_gas_*`) + dos `y_next`, sobre la **unión** de universos. Sin duplicar las compartidas; cada modelo selecciona sus columnas (filas fuera de su universo quedan con su `y_next` nulo).
   - **c) Columna `target`/partición** que discrimine las filas por modelo.
   - Recomendación de Rol 1: **(a)** por simplicidad y para que el universo train-only de cada target quede limpio; reusás `build_basic_dataset(target=...)` dos veces. Pero decidilo con Rol 3 según cómo le convenga leer en inferencia.

El paso de materialización de `ml/` (`feature_store_build.py`) ya reusa `add_engineered_features`; para gas hay que llamarlo con `target="prod_gas"` (y el universo/`y_next` correspondientes).
