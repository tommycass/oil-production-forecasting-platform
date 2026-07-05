# Handoff → Tommy: `/forecast` recursivo + retiro de `/predict` + modelo recursion-safe

**De:** Micol (ML) · **Contexto:** rediseño del forecast (ADR-044). Toca la API/serving y el modelo en MLflow.

## Qué cambió

### 1. `/forecast` ahora es un pronóstico ML real (recursivo mensual)
- Dejó de ser el mock de declinación lineal: encadena predicciones del modelo (predice t+1, **realimenta** esa predicción y sigue con t+2, t+3, …).
- **Contrato de Fase 1 intacto:** `id_well`, `date_start`, `date_end` → `{id_well, data:[{date, prod}]}`. Se agregó **un parámetro opcional `target`** (`prod_pet` por defecto / `prod_gas`), aditivo (quien no lo pasa obtiene petróleo, igual que antes).
- Salida **mensual** (un punto por mes, `date` = 1° de mes, **solo meses futuros**); horizonte acotado a **12 meses** desde el último dato del pozo (si el rango se pasa, **recorta**).
- Códigos: **404** (pozo sin serie en el store), **422** (rango sin meses futuros / `date_start > date_end` / target inválido), **503** (modelo no cargado en MLflow).
- Archivos: `ml/forecast.py` (**motor recursivo**, nuevo), `api/app/services/forecast.py`, `routes/forecast.py`, `schemas/forecast.py`, `feature_reader.py` (lee el store), `model_loader.py` (agregué `snapshot_model`).

### 2. `/predict` se **eliminó** (subsumido por `/forecast`)
- `/forecast` con un rango de **un mes** = exactamente lo que hacía `/predict`. Se borraron `routes/predict.py`, `schemas/predict.py`, `tests/test_predict.py` y su registro en `main.py`.
- **ADR-035 quedó reemplazado por ADR-044.** El gas se sigue sirviendo por `?target=prod_gas`.

### 3. El modelo es **recursion-safe por defecto** (MLflow)
- `ml/train.py` ahora entrena **siempre** con el set recursion-safe (35 features). El modelo que **registres/promuevas de acá en adelante es recursion-safe** — es el que `/forecast` necesita para poder recursar. El modelo viejo del ADR-040 (set completo de 40) **no sirve** para el recursivo.
- `BEST_PARAMS` re-tuneados sobre ese set (RF petróleo `200/24`, gas `400/16`, `min_samples_leaf=5`; ver `ml/modeling.py`). Números actualizados (val/test) en el **ADR-040** y el **README**.

## Qué tenés que hacer / verificar

1. **Registrar y promover a Production un modelo recursion-safe** por target: `python -m ml.train --mlflow --target prod_pet` (y `prod_gas`). Sin un modelo recursion-safe en Production, `/forecast` no recursa bien.
2. `/forecast` **depende del feature store re-materializado** (lo hace Valen, ver su handoff): sin las 35 columnas, el reader no encuentra las features.
3. **Verificar end-to-end** una vez que estén (a) el store re-materializado y (b) el modelo recursion-safe en Production: un `GET /api/v1/forecast?id_well=<n>&date_start=...&date_end=...` debería devolver la serie mensual.
4. Los tests de la API los reescribí (`api/tests/test_forecast.py`, mockean el store + el modelo). Corren en el **entorno combinado** (fastapi + pandas), no en el venv mínimo de la API (que no tiene pandas — es pre-existente).

## Referencias
- ADRs: **044** (forecast recursivo), **035** (reemplazado), **040** (modelo recursion-safe + números val/test), **038** (serving).
- `ml/forecast.py` (docstring explica la estrategia pre-computado vs on-the-fly).
