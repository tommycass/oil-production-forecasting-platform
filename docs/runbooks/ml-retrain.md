# Runbook — Reentrenamiento del modelo (retrain)

**Procedimiento:** operar el reentrenamiento del modelo de forecast: disparo automático
(Schedule + Sensor), retrain manual y **reproceso por fecha** (backfill cuando una fuente
corrige datos históricos).

Complementa el [ADR-041](../adr/0041-orquestacion-retrain.md) (orquestación del retrain),
el [ADR-036](../adr/0036-feature-store.md) (feature store) y el [contrato del feature store](../feature-store.md).

---

## 1. Propósito y disparador

Reentrenar **los dos modelos** (petróleo `prod_pet` y gas `prod_gas`, ADR-042) a partir de
features frescas y dejar los runs registrados en MLflow. El job `retrain` (Dagster,
particionado por día) encadena:

    features_refrescadas (materializa las 2 tablas del store) → modelo_reentrenado (entrena ambos targets + loguea a MLflow)

`modelo_reentrenado` corre el entrenamiento una vez por target (`--target prod_pet` /
`--target prod_gas`); cada uno usa su experimento/modelo de MLflow (`produccion-forecast` /
`produccion-forecast-gas`).

Se ejecuta cuando:
- **Cadencia mensual** (Schedule `retrain_mensual`, día 6): después del refresh del DW (ADR-021, cron día 5).
- **Llegan features nuevas** (Sensor `retrain_por_features_nuevas`): el feature store ganó un período.
- **Reproceso manual:** una fuente corrigió datos históricos y hay que reentrenar "como si fuera" una fecha pasada.

## 2. Rol / dueño y prerrequisitos

- **Dueño:** Rol 2 (Feature Store + Orquestación).
- **Accesos:** credenciales del DW (`POSTGRES_*` en `infra/.env`), repo en la EC2.
- **Venv del daemon** (`~/dagster-venv`): deps de `data_pipeline/requirements.txt` **+** las de `ml/requirements.txt` (mlflow, scikit-learn, xgboost, pandas, numpy). La materialización del store y el training reusan `ml/`. Instalar ambos: `pip install -r data_pipeline/requirements.txt -r ml/requirements.txt`.
- **`MLFLOW_TRACKING_URI`**: apuntar al servidor MLflow de Rol 3 (ADR-037). Si no se setea, el tracking cae al SQLite local de `ml/config.py`.
- **`DAGSTER_HOME`** (p. ej. `~/dagster-runtime`) para persistir runs y el cursor del sensor.

## 3. Setup del daemon (disparo automático)

El Schedule y el Sensor requieren el **dagster-daemon** corriendo (a diferencia del refresh del
DW, que es cron headless). En la EC2:

```bash
cd ~/oil-production-forecasting-platform
set -a; source infra/.env; set +a            # POSTGRES_*, MLFLOW_TRACKING_URI
source ~/dagster-venv/bin/activate
export DAGSTER_HOME=~/dagster-runtime; mkdir -p "$DAGSTER_HOME"

# Generar el manifest de dbt que consumen los assets del DW (ver run_pipeline.sh)
( cd transform && dbt deps && dbt parse --profiles-dir . --target-path "$PWD/target" )

# Daemon (ejecuta schedules y sensors) — dejar corriendo (nohup/systemd/tmux)
dagster-daemon run -m data_pipeline.orchestration.definitions
```

Para ver/operar desde la UI (opcional, más RAM): `dagster dev -m data_pipeline.orchestration.definitions`
y activar `retrain_mensual` / `retrain_por_features_nuevas` en *Automation*.

> El daemon consume RAM extra (t2.medium + swap). Si no se quiere 24/7, se levanta on-demand
> para demostrar/operar el retrain y se baja después.

## 4. Retrain manual (una fecha)

"Reentrenar como si fuera el día X" = materializar la partición X de los assets del retrain:

```bash
dagster asset materialize --select "features_refrescadas,modelo_reentrenado" \
  --partition 2026-04-06 -m data_pipeline.orchestration.definitions
```

> En Dagster 1.13 `dagster job execute` **no** acepta `--partition`; se usa `dagster asset
> materialize ... --partition` (o `dagster job execute -j retrain --tags '{"dagster/partition":"2026-04-06"}'`).

El paso de entrenamiento corre `RETRAIN_CMD` (default `python -m ml.baseline`, que ya loguea a
MLflow) **una vez por target** — el asset le agrega `--target prod_pet` y `--target prod_gas`
(ADR-042), así que una corrida reentrena los dos modelos. El campeón se entrena con
`RETRAIN_CMD="python -m ml.train ..."` **una vez que Rol 3 enchufe el logging de MLflow en
`train.py`** (hoy `train.py` no loguea; el tracking/registro es de Rol 3, ADR-037). La fecha de
corte llega en `RETRAIN_ASOF` y el entrenamiento la **honra**: `build_basic_dataset` recorta
`periodo <= asof`, así un reproceso de fecha pasada no usa datos posteriores (anti-leakage; ver
ADR-041). Si `asof` cae antes de `VAL_END`, los splits que aún no existen (p. ej. `test`) se omiten.

## 5. Reproceso por fecha / backfill (corrección histórica)

Cuando una fuente corrige datos de meses pasados, se reentrena el rango afectado. Dos caminos:

**a) Desde la UI / daemon (recomendado):** en *Assets* → `modelo_reentrenado` → *Materialize* →
elegir el **rango de particiones** (p. ej. `2025-01-01` … `2025-03-01`). El daemon lanza un run por partición.

**b) Headless (sin UI), iterando las fechas:**

```bash
for d in 2025-01-06 2025-02-06 2025-03-06; do
  echo "[retrain] reproceso $d"
  dagster asset materialize --select "features_refrescadas,modelo_reentrenado" \
    --partition "$d" -m data_pipeline.orchestration.definitions
done
```

Cada partición re-materializa el feature store (desde el Bronze ya corregido) y reentrena,
dejando un run por fecha en MLflow. Es **idempotente** (el store se reescribe y el run se vuelve a loguear).

> Precondición: el Bronze ya debe tener la corrección (corré antes el refresh del DW / `dw_publish`,
> ver runbook del Analytics Engineer). El retrain refresca **features** desde Bronze, no re-ingesta.

## 6. Verificación

- El run del job termina `Completed successfully` (UI o CLI).
- En MLflow aparece un run nuevo por fecha **en cada experimento** (`produccion-forecast` y `produccion-forecast-gas`) con sus métricas (RMSE/R²) y el modelo.
- Las **dos tablas** del store quedaron refrescadas con el último período de Bronze:
  ```sql
  SELECT 'pet' AS modelo, count(*), max(periodo) FROM features.feat_produccion_pozo_mensual
  UNION ALL
  SELECT 'gas', count(*), max(periodo) FROM features.feat_produccion_pozo_mensual_gas;
  ```
  (El universo gasífero es más amplio → la tabla de gas suele tener más filas.)
