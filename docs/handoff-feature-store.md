# Handoff — feature store realineado (Fase 3)

> **De:** Rol 2 (Feature Store + Orquestación). **Para:** Rol 1 (entrenamiento) y Rol 3 (inferencia).
> Acompaña al contrato [docs/feature-store.md](feature-store.md) y a [ADR-036](adr/0036-feature-store.md) (ver la sección *Revisión*).

## Por qué este handoff

Al integrar el modelado se detectó una **desalineación de tres puntas** en las features:
- **Training (Rol 1):** `train.py` → `build_basic_dataset` calcula **~29 features** en pandas desde el CSV (`ml/features.py`), sin pasar por el store.
- **Store (Rol 2):** la tabla daba **6** features viejas (`lag1/2/3`, `roll3`, `antiguedad`, `tef_lag1`).
- **Inferencia (Rol 3):** `feature_reader.py` lee esas 6 del store y se las pasa al modelo, que espera 29 → **inferencia rota + skew**.

**Solución acordada:** el store **materializa el pipeline de features de `ml/`** (única fuente de verdad). Un asset de Dagster (Rol 2) corre ese pipeline y escribe `features.feat_produccion_pozo_mensual`; training e inferencia leen lo mismo. Para que esto cierre sin skew, necesitamos coordinar lo siguiente.

## Pedido a Rol 1 (Rol 1.2 — features)

1. **Exponer `ml.dataset.build_serving_features()`** (o equivalente): features por `(idpozo, periodo)` **sin target ni split**, sobre **todos los meses** y los pozos con historia (no el universo train-only, que es para entrenar). Debe:
   - reutilizar `features.add_engineered_features` y las listas `BASIC_NUMERIC_FEATURES` / `BASIC_CATEGORICAL_FEATURES` (las mismas que usa el modelo);
   - devolver las columnas: claves (`idpozo`, `periodo`, `periodo_objetivo`) + las 29 features (8 numéricas + 7 engineered + 14 categóricas crudas);
   - **idealmente** que `build_basic_dataset` la reutilice (le agrega target + split encima), para que training y serving compartan **una sola** definición.
2. **Agregar `ml/requirements.txt`** (`mlflow`, `scikit-learn`, `pandas`, `numpy`, …): el venv que corre el training y la materialización del store lo necesita (hoy falta).

Con eso, el asset de materialización (Rol 2) llama a `build_serving_features()` y escribe el store; el training puede seguir usando `build_basic_dataset` (mismas features) o leer el store.

## Pedido a Rol 3 (inferencia)

`api/app/services/feature_reader.py` hoy lee **6 columnas viejas** (`lag1, lag2, lag3, roll3, antiguedad, tef_lag1`) que **dejan de existir**. Actualizar a las **columnas del modelo** (las 29 del contrato):

- **Lookup:** por `idpozo` + `periodo` del **mes base** `t` (= primer día de `mes_objetivo − 1 mes`), no por la columna `mes`.
- **Devolver todas las features** (numéricas + engineered + categóricas **crudas**) y pasárselas al `Pipeline` del modelo `Production` (el Pipeline hace el one-hot/imputación internamente).
- ⚠️ **`mes` es el mes del target (`t+1`)**, no el del mes base (así lo define `build_basic_dataset`). Por eso el lookup va por `periodo`.

La lista exacta de columnas está en [docs/feature-store.md](feature-store.md) (fuente de verdad: `ml.dataset.BASIC_*` + `ml.features.ENGINEERED_FEATURES`).

## Estado (Rol 2)

- Contrato y ADR actualizados a esta dirección.
- Asset de materialización: pendiente de que Rol 1 exponga `build_serving_features()` (sin eso no se puede correr ni validar end-to-end). El modelo dbt actual del store se reemplaza recién cuando la materialización esté validada (para no dejar el store sin tabla).
