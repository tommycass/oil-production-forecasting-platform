# Handoff — feature store materializado (Fase 3)

> ⚠️ **HISTÓRICO (jul-2026):** los pedidos de este documento ya se cumplieron y el contrato
> evolucionó varias veces: `/predict` fue subsumido por `/forecast` (ADR-044) y el store quedó
> en el **set de ganancia positiva — 27 features (petróleo) / 19 (gas)** (ADR-043, revisado por
> [ADR-046](adr/0046-seleccion-features-ganancia-positiva.md)) + las tablas de
> **pronóstico precomputado** (ADR-045). El contrato vigente es [docs/feature-store.md](feature-store.md);
> handoffs vigentes en `docs/handoffs/`. Se conserva como registro de la integración.

> **De:** Rol 2 (Feature Store + Orquestación). **Para:** Rol 1 (entrenamiento) y Rol 3 (inferencia).
> Acompaña al contrato [docs/feature-store.md](feature-store.md) y a [ADR-036](adr/0036-feature-store.md) (sección *Revisión*). Responde al handoff de Rol 1 en [docs/handoffs/rol2-feature-store.md](handoffs/rol2-feature-store.md).

## Qué hicimos

Materializamos el feature store con **exactamente las features del modelo** (las 29), **reusando el código de `ml/`** como pide el handoff de Rol 1 (única fuente de verdad → cero skew). **Son dos modelos (ADR-042) → dos tablas, una por target** (decisión registrada en ADR-036 *Revisión 2*):

- Un asset de Dagster (`features_refrescadas`, en el job de retrain) corre el pipeline de features (`data_pipeline/orchestration/feature_store_build.py`, que importa `ml.features.add_engineered_features(target=...)` y las listas `ml.dataset.BASIC_*`) y escribe **las dos tablas**:
  - `features.feat_produccion_pozo_mensual` — petróleo (`prod_pet`, nombre histórico).
  - `features.feat_produccion_pozo_mensual_gas` — gas (`prod_gas`).
- Cada tabla tiene **su universo** (pozos con ese target `> 0` en train; el gasífero es más amplio), **sus engineered** sobre el target (`prod_pet_*` vs `prod_gas_*`) y su `y_next`. Las 8 numéricas base + 14 categóricas son compartidas (se duplican). `build_store_features(df, target=...)` se parametrizó por target; `materializar_todos` recorre `ml.config.TARGETS`.
- **Fuente:** `bronze.produccion` (crudo, mismas columnas que el CSV del training; gobernado en el DW). Se lee **una sola vez** y se construyen las dos tablas.
- **Paridad validada (§8 del handoff de Rol 1):** comparado contra `build_basic_dataset(target=...)` para **los dos targets** → **0 diferencias** en las features ni en `y_next` (mismo código de `ml/`). Cada tabla = 39 columnas (3 claves + 35 features recursion-safe + `y_next`), tipos correctos.
- **Diferencia con training:** el target va por **left-join**, así se conserva la **última fila de cada pozo** (`y_next` NULL) para que la API pueda predecir el mes siguiente. El training usa las filas con `y_next` no nulo (idéntico a `build_basic_dataset`).

## Pendiente de Rol 1 (A)

1. ~~Agregar `ml/requirements.txt`~~ **HECHO** (ya está en el repo: `mlflow`, `scikit-learn`, `pandas`, `numpy`, `xgboost`, …). El venv del daemon de retrain lo instala (la materialización del store y el training reusan `ml/`).
2. ~~Honrar `RETRAIN_ASOF` en el train step (necesario para el reproceso por fecha).~~ **HECHO.** `ml.config.retrain_asof()` lee la env var `RETRAIN_ASOF` (`YYYY-MM-DD`) y `build_basic_dataset(..., asof=...)` **recorta `periodo <= asof`** *antes* de definir el universo y las features (así todo respeta el corte, anti-leakage del backfill). Como `baseline`/`train` arman el dataset por ahí, ambos lo honran automáticamente sin tocar el comando del retrain. Además, los baselines **toleran splits vacíos** (si `asof` es anterior a `VAL_END`, el `test`/`val` que aún no existe se **omite** en vez de reventar). Sin la env var, el comportamiento es idéntico al de antes (verificado: persistencia val RMSE 250,6). *No* movemos `TRAIN_END`/`VAL_END`: el split fijo del ADR-028 se mantiene y los períodos inexistentes a esa fecha simplemente se saltean.
3. **Opcional — conectar el training al store:** podés seguir con `build_basic_dataset(target=...)` (mismas features, paridad validada en ambos targets) o leer la tabla del target (filas con `y_next` no nulo). Si cambiás la lista de features en `ml/`, el asset la toma automáticamente (importa tus listas) — avisanos para re-materializar y que C ajuste el reader.

## Pendiente de Rol 3 (C) — inferencia

`api/app/services/feature_reader.py` quedó **desactualizado en 3 frentes** (no es solo cambiar nombres de columnas): pide 6 columnas que ya no existen, filtra por `anio`/`mes` (el store **no tiene** `anio`, y `mes` es el mes del **target** `t+1`), y usa una sola tabla. Hay que **reescribir el lookup**:

**Hoy (roto):**
```sql
SELECT lag1, lag2, lag3, roll3, antiguedad, tef_lag1
FROM features.feat_produccion_pozo_mensual
WHERE idpozo = :idpozo AND anio = :anio AND mes = :mes
```

**Esperado:**
```sql
SELECT <las 35 features>           -- o SELECT * y descartar periodo_objetivo / y_next
FROM features.feat_produccion_pozo_mensual        -- petróleo
-- o features.feat_produccion_pozo_mensual_gas    -- gas
WHERE idpozo = :idpozo AND periodo = :periodo_t   -- periodo_t = date(mes_objetivo) - 1 mes
```

Checklist:
- [ ] **Tabla por target:** `feat_produccion_pozo_mensual` (petróleo) / `feat_produccion_pozo_mensual_gas` (gas), y cargar el modelo `Production` del registry **de ese target** (`produccion-forecast` / `produccion-forecast-gas`).
- [ ] **Lookup por `idpozo` + `periodo`** (date) del **mes base** `t` = primer día de `mes_objetivo − 1 mes`. **No** por `anio`/`mes` (no existe `anio`; `mes` es el mes del target).
- [ ] **Pasar las 35 features crudas** (5 numéricas + 16 engineered + 14 categóricas) al `Pipeline` del modelo —que hace one-hot/imputación internamente—; **descartar** `idpozo`, `periodo`, `periodo_objetivo`, `y_next`. Los nombres engineered **difieren por target** (`prod_pet_*` vs `prod_gas_*`): conviene derivar la lista de `ml.dataset.BASIC_NUMERIC_FEATURES + ml.features.engineered_feature_names(target) + ml.dataset.BASIC_CATEGORICAL_FEATURES` (única fuente de verdad) en vez de hardcodear.
- [ ] **Error de contrato** si no hay fila para `(idpozo, periodo_t)` (pozo nuevo o sin historia) — como hoy, pero con la clave correcta.
- [ ] **Exponer el target en `/predict`** (un parámetro `target=prod_pet|prod_gas` o dos rutas): decisión tuya; revisar [ADR-035] (contrato de `/predict`) como anota el ADR-042.

Lista completa de columnas y tipos: [docs/feature-store.md](feature-store.md).
