# Handoff Rol 1 → Rol 2: Feature store del forecast de producción

**De:** Rol 1 (ML Engineer) · **Para:** Rol 2 (Feature store + orquestación)
**Objetivo:** que materialices en el feature store **exactamente las mismas features** que usa el entrenamiento, calculadas de la misma forma, para evitar *training-serving skew*.

Todo lo de acá está implementado y validado en `ml/` y documentado en los ADR-028, 031, 032, 033, 036, 037.

---

## 1. Qué predice el modelo (contexto mínimo)

- **Target:** `prod_pet` (m³ de petróleo) del **mes siguiente** (`y_next` = producción en `t+1`).
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

Todas se calculan **por pozo**, con **lags por calendario** (no `shift` de filas). Insumos: `prod_pet`, `prod_agua` (mensuales) y `coordenadax/coordenaday` (estáticos).

| Feature | Cálculo (sobre la serie del pozo) |
|---|---|
| `prod_pet_roll3` | media de `prod_pet` en {t, t-1, t-2} |
| `prod_pet_delta1` | `prod_pet(t) − prod_pet(t-1)` |
| `prod_pet_lag12` | `prod_pet(t-12)` (mismo mes del año anterior) |
| `prod_pet_acum6` | suma de `prod_pet` en {t … t-5} |
| `water_cut` | `prod_agua / (prod_agua + prod_pet)` en t; `0` si el denominador es 0 |
| `produjo_mes_pasado` | `1` si `prod_pet(t) > 0`, si no `0` |
| `prod_vecinos_mean` | media de `prod_pet(t)` de los **5 pozos más cercanos** por (`coordenadax`,`coordenaday`), excluido el propio pozo |

### 2.3 Categóricas (14) — atributos, valor en el mes `t`

`tipoextraccion`, `tipoestado`, `tipopozo`, `empresa`, `formprod`, `formacion`, `areapermisoconcesion`, `areayacimiento`, `cuenca`, `provincia`, `proyecto`, `clasificacion`, `subclasificacion`, `sub_tipo_recurso`.

Se guardan **como texto crudo** (sin codificar). El one-hot **no** va en el store: vive en el `Pipeline` del modelo (ver §5).

### 2.4 Target

`y_next` = `prod_pet` del mes `t+1` (para entrenar). En **inferencia** no se materializa (es lo que se predice).

---

## 3. Reglas anti-leakage que el store DEBE respetar

Son la parte crítica. Si se rompen, el modelo entrena con información que no tendría en producción.

1. **Features solo de `t` o anterior.** Ninguna feature puede usar datos de `t+1` (el mes que se predice). Las medidas son de `t`; los lags/ventanas, de `t` hacia atrás.
2. **Lags por calendario, no por fila.** `t-k` se busca por `periodo - k meses` (no tomar "la fila anterior"); si falta ese mes, queda `NaN`. Replicar `ml.features._calendar_lag`. Esto evita que un hueco en la serie corra el lag.
3. **`mes` es el mes del target (`t+1`)**, no el de las medidas. Es la única excepción "del futuro" y es válida porque la fecha a predecir se conoce de antemano (determinística, no es leakage).
4. **Target por merge de calendario** (`periodo + 1 mes`), no por `shift(-1)`: garantiza que `y_next` sea siempre exactamente el mes siguiente.
5. **Universo train-only.** El conjunto de pozos = los que tienen `prod_pet > 0` en algún mes **≤ TRAIN_END** (`2023-07-01`). No definir el universo con todo el histórico (metería pozos que recién producen en val/test → leakage de selección).
6. **Descartar producción negativa.** Filas con `prod_pet`/`prod_gas`/`prod_agua < 0` son errores de dato → se eliminan (ADR-036).

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
- (No hay clipping de outliers ni transformación del target — decidido con evidencia, ADR-036.)

→ El store materializa las **features crudas** (las 29 + `y_next`). El modelo se encarga del resto. Así, si Rol 3 sirve el modelo, solo tiene que leer estas mismas features del store y pasárselas al `Pipeline`.

---

## 6. Split temporal (para que entrenamiento y reproceso coincidan)

- `train`: `periodo <= 2023-07-01`
- `val`: `2023-07-01 < periodo <= 2024-11-01`
- `test`: `periodo > 2024-11-01`

(`ml/config.py`: `TRAIN_END`, `VAL_END`.) Para el **retrain parametrizable por fecha** (tu tarea 2.3), mover `TRAIN_END`/`VAL_END` hacia adelante reincorpora pozos nuevos al universo y categorías nuevas al one-hot.

---

## 7. Código de referencia (fuente de verdad)

- `ml/features.py` — las 7 features de ingeniería (replicá esta lógica en el store).
- `ml/dataset.py` — `build_basic_dataset` (universo, drop de negativos, merge del target, split) y las listas `BASIC_NUMERIC_FEATURES` / `BASIC_CATEGORICAL_FEATURES`.
- `ml/config.py` — `TARGET`, `TRAIN_END`, `VAL_END`.
- ADR-031 (dataset anti-leakage), ADR-033 (features), ADR-036 (preprocesamiento).

## 8. Cómo validar paridad store ↔ training

Generá las features con tu pipeline para un set de `(idpozo, periodo)` y comparalas contra las de `build_basic_dataset` (mismas columnas, mismos valores). Diferencias = skew a corregir antes de servir.

---

## 9. Decisión conjunta pendiente

Acordemos el **esquema final del store** (nombres/tipos exactos y el linaje a Gold de cada columna), que es lo que desbloquea también a Rol 3 para la inferencia.
