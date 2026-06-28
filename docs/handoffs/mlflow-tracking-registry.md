# Handoff Rol 1 → MLflow: experiment tracking (1.4) y model registry (1.5)

**De:** Rol 1 (ML Engineer) · **Para:** quien implemente MLflow
**Objetivo:** que `train.py` registre cada corrida en MLflow (params, métricas, versión de datos, modelo) y que el campeón se versione y promueva a Production con un criterio definido.

El modelado y la decisión del campeón **ya están**; falta **enchufar MLflow** alrededor. El código de entrenamiento ya devuelve todo lo necesario para loguear (el "gancho").

---

## 1. Lo que YA está hecho

- **ADR-030 — Plataforma de tracking (MLflow vs W&B vs otras):** decidido MLflow self-hosted. *(Cubre el ADR sugerido "Plataforma de tracking"; no hace falta reescribirlo.)*
- **ADR-034 / ADR-040 — Algoritmo y modelo a producción:** decididos y justificados. *(Cubren el ADR sugerido "Tipo de modelo elegido".)* Campeón = **Random Forest tuneado**.
- **Config MLflow** (`ml/config.py`): `MLFLOW_TRACKING_URI` (SQLite local, override por env var `MLFLOW_TRACKING_URI`), `MLFLOW_ARTIFACT_URI` (`./mlartifacts`), `EXPERIMENT_NAME = "produccion-forecast"`.
- **Setup helper** (`ml/tracking.py`): `setup_mlflow()` — apunta al backend y asegura el experimento. Idempotente.
- **Baselines ya logueados** (`ml/baseline.py`): `python -m ml.baseline` loguea persistencia / media-móvil / estacional como runs (params + métricas val/test). **Es el patrón de logging a imitar** para los modelos.
- **Criterio de promoción definido** (ADR-040): un modelo pasa a Production si **supera a la persistencia en RMSE en val** y lo confirma en **test**; entre candidatos gana el de menor val RMSE; se re-evalúa en cada reentreno.
- **`train.py` deja el gancho:** entrena y **devuelve `(pipeline, info)`** con todo lo que hay que loguear (ver §3). Hoy **no** loguea en MLflow a propósito (te lo dejé a vos).

## 2. Lo que FALTA

### 1.4 — Experiment tracking en `train.py`
`train.py` todavía **no registra en MLflow**. Falta loguear, por cada run:
- [ ] **Parámetros del modelo** → `info["best_params"]` (o `info["params"]` en el final).
- [ ] **Métricas de evaluación** → `info["train"]`, `info["val"]` (y `info["test"]` en el final), más la persistencia de referencia.
- [ ] **Versión de los datos usados** → no existe aún. Sugerencia: loguear `TRAIN_END`, `VAL_END`, la fuente (`DATA_CSV` o, cuando exista, la snapshot/versión del feature store del Rol 2), `n_dev`/`n_test`, y un hash del dataset.
- [ ] **Modelo entrenado** → el `pipeline` devuelto (incluye preprocesamiento), con `mlflow.sklearn.log_model(pipe, ...)`.

### 1.5 — Model registry
- [ ] **Registrar** el modelo (`registered_model_name="produccion-forecast"`) y **versionar** (v1, v2, …).
- [ ] **Stages** `Staging → Production`.
- [ ] **Automatizar la promoción** con el criterio del ADR-040: comparar la métrica del modelo nuevo contra la del Production actual y promover solo si mejora (y supera la persistencia).
- [ ] **Persistir los hiperparámetros junto a cada versión, de forma estandarizada** (ver §2.1): cada versión del registry debe poder responder "con qué hiperparámetros se entrenó" sin mirar el código.

#### 2.1 — Hiperparámetros versionados (estándar a respetar)

Hoy los mejores hiperparámetros viven **hardcodeados** en `ml/modeling.py` (`BEST_PARAMS`) como "últimos mejores registrados" — es un **placeholder** hasta que exista el registry. El objetivo es que **la fuente de verdad pase a ser el registry**, con los params guardados de forma estandarizada y prolija para cada versión:

- [ ] **Esquema de nombres único y plano:** loguear cada hiperparámetro con `mlflow.log_params(...)` usando **siempre las mismas claves** (las de `BEST_PARAMS[modelo]`: `n_estimators`, `max_depth`, `max_features`, `min_samples_leaf`, …). Así dos versiones se comparan campo a campo en la UI/`search_runs`.
- [ ] **Atados a la versión, no solo al run:** además de loguearlos en el run, dejarlos accesibles desde la **versión registrada** (tags de la model version o el run linkeado), para que `get_model_version(n)` permita recuperar sus params.
- [ ] **Dentro del artefacto:** que los params viajen con el modelo serializado (los guarda `mlflow.sklearn.log_model` en el `MLmodel`/pipeline; opcionalmente un `params.json` como artifact legible), así el modelo es **autodescriptivo** aunque se mueva de backend.
- [ ] **Trazabilidad de origen:** un tag indicando si los params salieron del **tuning** (random search, ADR-034) o son **fijos** (`BEST_PARAMS`), más la `random_state` (ver `config.RANDOM_STATE`) para reproducibilidad.
- [ ] **Cerrar el loop:** una vez versionados en el registry, `train.py` (modo `tune=False`) debería **leer los params de la última versión `Production`** en lugar de `modeling.BEST_PARAMS`; ahí `BEST_PARAMS` queda solo como fallback/bootstrap inicial.

> El **servidor** MLflow (Docker/infra) es del Rol 3 — ver **ADR-037 (mlflow-server)** y **ADR-038 (serving)**. Coordiná con ellos el backend (Postgres) y el artifact store reales; el código usa `MLFLOW_TRACKING_URI` por env var, así que apuntarlo al servidor no requiere tocar nada.

## 3. El gancho concreto (qué devuelve el entrenamiento)

```python
from ml import train

# entrenamiento normal (tunea por defecto; o tune=False usa BEST_PARAMS):
pipe, info = train.train("random_forest", tune=True)
# info = {model, tuned, best_params, train:{val_rmse,val_r2}, val:{...}, persistencia_val:{...}}

# entrenamiento final para producción (dev=train+val) + evaluación en test:
pipe, info = train.train_final("random_forest")
# info = {model, params, n_dev, n_test, dev:{...}, test:{...}, persistencia_test:{...}}
```

`pipe` es un `sklearn.Pipeline` completo (preprocesamiento + modelo) → es el artefacto a loguear/registrar.

## 4. Sketch de implementación sugerido

```python
import mlflow, mlflow.sklearn
from ml.tracking import setup_mlflow
from ml.config import TRAIN_END, VAL_END
from ml import train

setup_mlflow()
pipe, info = train.train_final("random_forest")
with mlflow.start_run(run_name="rf_final"):
    mlflow.set_tag("modelo", info["model"])
    mlflow.log_params(info["params"])
    # versión de datos:
    mlflow.log_param("train_end", str(TRAIN_END))
    mlflow.log_param("val_end", str(VAL_END))
    mlflow.log_param("n_dev", info["n_dev"])
    mlflow.log_param("n_test", info["n_test"])
    # métricas:
    mlflow.log_metric("test_rmse", info["test"]["val_rmse"])
    mlflow.log_metric("test_r2", info["test"]["val_r2"])
    mlflow.log_metric("persistencia_test_rmse", info["persistencia_test"]["val_rmse"])
    # modelo (+ registro):
    mlflow.sklearn.log_model(pipe, "model", registered_model_name="produccion-forecast")
# promoción: si test_rmse < persistencia_test_rmse y mejora al Production actual -> set stage "Production"
```

Lo ideal es encapsular esto en `train.py` (p. ej. un flag `--mlflow` o una función `log_run(pipe, info)`) para no duplicar la lógica.

## 5. Datos de referencia (números actuales)

- Campeón: **Random Forest** `{n_estimators=400, max_depth=16, max_features=0.5, min_samples_leaf=2}`.
- Test (modelo final, dev→test): **RMSE 154,4 / R² 0,874**; persistencia test 166,2 / 0,854.
- Val (tuneado): RF 229,9 / 0,900.
- Baseline a batir (persistencia, ADR-029).

## 6. Archivos de referencia

- `ml/config.py` — config MLflow (URIs, experimento).
- `ml/tracking.py` — `setup_mlflow()`.
- `ml/baseline.py` — **ejemplo de logging** (params + métricas como runs).
- `ml/train.py` — `train()` / `train_final()` (el gancho, devuelven `info`).
- ADR-030 (plataforma), ADR-040 (campeón + criterio de promoción), ADR-037/038 (infra y serving — Rol 3).
