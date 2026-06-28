# Feature Store — contrato (Fase 3)

> **Dueño:** Rol 2 (Feature Store + Orquestación). **Consumidores:** Rol 1 (entrenamiento) y Rol 3 (inferencia).
> Este documento es el **contrato estable** del feature store. Decisión y alternativas: [ADR-031](adr/0031-feature-store.md). Diseño del problema: [ADR-028](adr/0028-diseno-problema-modelado.md).

## Por qué existe

La consigna de Fase 3 (RNF) exige que **el procesamiento y la generación de features quede persistido en un feature store**, y que **las mismas features sirvan para entrenamiento e inferencia**. Esto evita el **training-serving skew**: si el modelo se entrena con features calculadas de una forma y la API las recalcula de otra, las predicciones en producción difieren de las de entrenamiento. La solución: **calcular las features una sola vez** y que tanto el training como la API **lean de la misma tabla**.

## Qué es

Una tabla materializada en el Data Warehouse (PostgreSQL), generada por dbt a partir de la capa **Gold** (`fact_produccion_mensual` + dimensiones). Es un **feature store offline** (una tabla, no un servicio dedicado; ver ADR-031).

| | |
|---|---|
| **Tabla** | `features.feat_produccion_pozo_mensual` |
| **Materialización** | `table` (dbt) |
| **Grano** | una fila por `(idpozo, anio, mes)` = el **mes base `t`** |
| **Universo** | pozos petroleros = con al menos un mes de `prod_pet > 0` (ADR-028) |
| **Linaje** | Bronze → Silver → Gold → **Features** (trazado en DataHub) |
| **Refresh** | parte de `dbt build` (corre con el DW); el job de retrain la refresca antes de entrenar |

## Encuadre temporal (Convención: fila = mes base)

Cada fila representa el **mes base `t`**: sus features están **disponibles al cierre de `t`** (anti-leakage) y el target `y_next` es la producción del **mes siguiente `t+1`**.

```
fila (idpozo, t):  [ features as-of t ]  →  predice  y_next = prod_pet(t+1)
```

- **Entrenamiento:** se usan las filas con `y_next IS NOT NULL` (el par features→target es conocido).
- **Inferencia de `t+1`:** se toma la fila del **mes base `t`** (la última observada del pozo) y se le pasan sus features al modelo. **La API no recalcula features**: las lee de acá.

## Diccionario de columnas

### Claves / entidad
| Columna | Tipo | Definición |
|---|---|---|
| `idpozo` | int | Identificador del pozo. |
| `anio`, `mes` | int | Mes base `t`. |
| `periodo` | date | Primer día de `t` (`make_date(anio, mes, 1)`). Para el split temporal del modelo. |
| `trimestre` | int | Trimestre de `t` (estacionalidad). |

### Atributos estáticos del pozo (categóricas **crudas**)
| Columna | Origen Gold | Nota |
|---|---|---|
| `profundidad` | `dim_pozo` | numérica |
| `formacion` | `dim_pozo` | categórica |
| `tipopozo` | `dim_pozo` | categórica, **normalizada a UPPER** (`PETROLÍFERO`, `GASÍFERO`, …) |
| `clasificacion` | `dim_pozo` | categórica |
| `cuenca`, `provincia` | `dim_yacimiento` | categóricas |
| `operadora` | `dim_operadora` | categórica |

> Las categóricas se exponen **crudas**. El encoding (one-hot / target encoding / scaling) lo hace el modelo (`train.py`) y se serializa en el artefacto de MLflow, para que la inferencia lo replique idéntico (ADR-031). El store **no** impone un encoding.

### Medidas crudas del mes `t`
| Columna | Definición |
|---|---|
| `prod_pet`, `prod_gas`, `tef` | producción de petróleo/gas y tiempo efectivo del mes `t`. |

### Features autoregresivas (anti-leakage, as-of `t`)
Reproducen **exacto** las definiciones de `ml/dataset.py` (shifts/rolling/cumcount posicionales por fila ordenada por `(anio, mes)`):

| Columna | Definición (pandas equivalente) |
|---|---|
| `lag1`, `lag2`, `lag3` | `prod_pet` de la 1ª/2ª/3ª fila anterior del pozo (`groupby(idpozo).prod_pet.shift(1\|2\|3)`). |
| `roll3` | media móvil de `prod_pet` de los últimos 3 meses; **NULL** hasta tener 3 (`rolling(3).mean()`, min_periods=3). |
| `antiguedad` | meses de historia del pozo hasta `t`, **0-based** (`cumcount()`). |
| `tef_lag1` | `tef` de la fila anterior (`groupby(idpozo).tef.shift(1)`). |

> **Lags posicionales (no calendario):** si un pozo tiene un hueco de meses, `lag1` es la fila anterior existente (no el calendario `t-1`). Es idéntico a `shift(1)` de pandas; consistente entre training e inferencia.

### Target
| Columna | Definición |
|---|---|
| `y_next` | `prod_pet` del mes siguiente `t+1` (`shift(-1)`). **NULL** en el último mes de cada pozo (sin observar) → esas filas son para inferencia, no entrenamiento. |

### Metadata (reproducibilidad)
| Columna | Definición |
|---|---|
| `feature_set_version` | versión del contrato de features (`v1`). Bump al cambiar definiciones. |
| `computed_at` | timestamp de materialización de la tabla. |

## Cómo consumir

### Rol 1 — entrenamiento (`ml/dataset.py`)
La fuente provisoria (`data/_explore/produccion_full.csv`, `ml/config.DATA_CSV`) se **reemplaza por una lectura de esta tabla**. Como las columnas `lag1/lag2/lag3/roll3/antiguedad/tef_lag1/y_next` ya vienen calculadas y con los **mismos nombres**, el swap es directo:

- `load_production` + `add_features` + `add_target` → `SELECT ... FROM features.feat_produccion_pozo_mensual`.
- `add_split` (train/val/test por `periodo`) **se mantiene en `ml/`** (depende de las fechas de corte del ADR-028, que viven en `ml/config.py`).
- `add_baselines` puede seguir calculándose desde `prod_pet` (los baselines no se sirven, no generan skew).

### Rol 3 — inferencia (`POST /api/v1/predict`)
Dado un `(idpozo, mes_objetivo)`: leer la fila del **mes base** `t = mes_objetivo - 1` de esta tabla, tomar las columnas de features y pasarlas al modelo marcado `Production` en MLflow. **No recalcular features.** Si no existe fila para ese pozo/mes (pozo sin historia suficiente), responder el error de contrato definido por Rol 3.

## Versionado y cambios

Cambiar una definición de feature (o agregar/quitar columnas que el modelo consume) es un **cambio de contrato**: se incrementa `feature_set_version`, se actualiza este documento y se avisa a Rol 1 y Rol 3. Las columnas extra (no consumidas hoy por el modelo) pueden agregarse de forma aditiva sin romper a los consumidores.
