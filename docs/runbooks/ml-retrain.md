# Runbook — Reentrenamiento del modelo (retrain)

**Procedimiento:** operar el reentrenamiento del modelo de forecast: disparo automático
(Schedule + Sensor), retrain manual y **reproceso por fecha** (backfill cuando una fuente
corrige datos históricos).

Complementa el [ADR-040](../adr/0040-orquestacion-retrain.md) (orquestación del retrain),
el [ADR-035](../adr/0035-feature-store.md) (feature store) y el [contrato del feature store](../feature-store.md).

---

## 1. Propósito y disparador

Reentrenar **los dos modelos** (petróleo `prod_pet` y gas `prod_gas`, ADR-039) a partir de
features frescas y dejar los runs registrados en MLflow. El job `retrain` (Dagster,
particionado por día) encadena:

    features_refrescadas (materializa las 2 tablas del store)
      → modelo_reentrenado (entrena ambos targets + loguea a MLflow)
        → forecast_precomputado (precomputa el pronóstico de 12 meses por pozo, ADR-043)

`modelo_reentrenado` corre el entrenamiento una vez por target (`--target prod_pet` /
`--target prod_gas`); cada uno usa su experimento/modelo de MLflow (`produccion-forecast` /
`produccion-forecast-gas`). `forecast_precomputado` escribe
`features.pred_produccion_pozo_mensual` (+ `_gas`) con el modelo **Production** de cada
target; la API sirve esas filas como lookup (con fallback on-the-fly). Si un target no
tiene modelo en Production, el asset lo saltea con warning (no falla el job).

Se ejecuta cuando:
- **Cadencia mensual** (Schedule `retrain_mensual`, día 6): después del refresh del DW (ADR-021, cron día 5).
- **Llegan features nuevas** (Sensor `retrain_por_features_nuevas`): el feature store ganó un período.
- **Reproceso manual:** una fuente corrigió datos históricos y hay que reentrenar "como si fuera" una fecha pasada.

## 2. Rol / dueño y prerrequisitos

- **Dueño:** Rol 2 (Feature Store + Orquestación).
- **Accesos:** credenciales del DW (`POSTGRES_*` en `infra/.env`), repo en la EC2.
- **Venv del daemon** (`~/dagster-venv`): deps de `data_pipeline/requirements.txt` **+** las de `ml/requirements.txt` (mlflow, scikit-learn, xgboost, pandas, numpy). La materialización del store y el training reusan `ml/`. Instalar ambos: `pip install -r data_pipeline/requirements.txt -r ml/requirements.txt`.
- **`MLFLOW_TRACKING_URI`**: apuntar al servidor MLflow de Rol 3 (ADR-036). Si no se setea, el tracking cae al SQLite local de `ml/config.py`.
- **`DAGSTER_HOME`** (p. ej. `~/dagster-runtime`) para persistir runs y el cursor del sensor.

## 3. Setup del daemon (disparo automático)

El Schedule y el Sensor requieren el **dagster-daemon** corriendo (a diferencia del refresh del
DW, que es cron headless). En la EC2:

```bash
cd ~/oil-production-forecasting-platform
set -a; source infra/.env; set +a            # POSTGRES_*, MLFLOW_TRACKING_URI
source ~/dagster-venv/bin/activate
export DAGSTER_HOME=~/dagster-runtime; mkdir -p "$DAGSTER_HOME"

# Comando de entrenamiento del retrain automático: entrena el campeón, lo registra y
# lo promueve si supera al vigente (ADR-039/040). Sin esto, el default (ml.baseline)
# solo loguea baselines y NO despliega modelo → el retrain no sería "despliegue automático".
export RETRAIN_CMD="python -m ml.train --mlflow"

# Generar el manifest de dbt que consumen los assets del DW (ver run_pipeline.sh)
( cd transform && dbt deps && dbt parse --profiles-dir . --target-path "$PWD/target" )

# Daemon (ejecuta schedules y sensors) — dejar corriendo (nohup/systemd/tmux)
dagster-daemon run -m data_pipeline.orchestration.definitions
```

El Schedule y el Sensor quedan **activos por defecto** (`default_status=RUNNING` en
`retrain.py`): apenas el daemon arranca, disparan solos — no hay que prenderlos a mano.
Para ver/operar desde la UI (opcional, más RAM): `dagster dev -m data_pipeline.orchestration.definitions`
(en *Automation* aparecen ya en verde; ahí se pueden pausar si hace falta).

> El daemon consume RAM extra (t2.medium + swap). Si no se quiere 24/7, se levanta on-demand
> para demostrar/operar el retrain y se baja después.

## 4. Retrain manual (una fecha)

"Reentrenar como si fuera el día X" = materializar la partición X de los assets del retrain:

```bash
dagster asset materialize --select "features_refrescadas,modelo_reentrenado,forecast_precomputado" \
  --partition 2026-04-06 -m data_pipeline.orchestration.definitions
```

> En Dagster 1.13 `dagster job execute` **no** acepta `--partition`; se usa `dagster asset
> materialize ... --partition` (o `dagster job execute -j retrain --tags '{"dagster/partition":"2026-04-06"}'`).

El paso de entrenamiento corre `RETRAIN_CMD` (default `python -m ml.baseline`, que ya loguea a
MLflow) **una vez por target** — el asset le agrega `--target prod_pet` y `--target prod_gas`
(ADR-039), así que una corrida reentrena los dos modelos. Para reentrenar y **registrar/promover
el campeón** en cada corrida (Rol 3 ya enchufó el logging en `train.py`, 1.4/1.5) se exporta:

```bash
export RETRAIN_CMD="python -m ml.train --mlflow"   # loguea, registra y promueve (ADR-039)
```

`ml.train --mlflow` entrena el campeón final, loguea el run (params, métricas dev/test, versión
de datos, `Pipeline`), lo registra como nueva versión del modelo del target y lo promueve a
`Production` si supera a la persistencia y mejora al `Production` actual —re-evaluado en vivo
sobre la misma ventana de test (`ml/registry.py`); `--no-promote` lo deja en `Staging`. La fecha
de corte llega en `RETRAIN_ASOF`: el store se re-materializa recortado a `periodo <= asof` y el
entrenamiento lo lee ya recortado (anti-leakage del backfill; ver ADR-040). Como los cortes del
split se **derivan** de esa fecha (ADR-028), un `asof` pasado **corre toda la ventana** train/val/test
en vez de dejar splits vacíos → el modelo reentrena sobre el slice de ese día.

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

> Podés seleccionar el job completo: en un reproceso histórico `forecast_precomputado` se
> **saltea solo** (el precómputo es para servir hoy, no para una fecha pasada, ADR-040).

Un reproceso de una fecha **anterior** al último período es un backfill histórico y se **aísla**
para no pisar el serving (ADR-040): materializa el store en tablas `..._backfill`, entrena
leyéndolas y **no promueve** (un modelo con datos viejos no debe pisar Production), y deja el run
en MLflow para reproducibilidad. Las tablas en vivo y el precómputo **no se tocan**. Es
idempotente. Reprocesar la fecha de **hoy** sí actualiza el serving (corrida normal).

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
- El **precómputo** quedó fresco y alineado al store (12 filas por pozo, arrancando en el
  mes siguiente al último observado):
  ```sql
  SELECT count(*) AS filas, count(DISTINCT idpozo) AS pozos,
         min(periodo) AS primer_mes, max(model_version) AS version
  FROM features.pred_produccion_pozo_mensual;
  -- frescura: debe dar 0 (todos los pozos precomputados desde su último mes real)
  SELECT count(*) FROM (
    SELECT f.idpozo, max(f.periodo) AS ultimo, max(p.ultimo_observado) AS visto
    FROM features.feat_produccion_pozo_mensual f
    JOIN features.pred_produccion_pozo_mensual p USING (idpozo)
    GROUP BY f.idpozo
  ) x WHERE ultimo <> visto;
  ```
- Sanity de la API: `GET /api/v1/forecast` de un pozo con precómputo responde igual que
  siempre (contrato de Fase 1); si se borra la tabla `pred_*`, responde idéntico vía
  motor on-the-fly (transparencia, ADR-043).

## 7. Promoción manual de modelo → refrescar el precómputo

El precómputo se genera con el Production vigente **al momento del retrain**. Si se
promueve un modelo **a mano** fuera del ciclo (p. ej. desde la UI de MLflow), el lookup
sigue sirviendo lo calculado con el modelo anterior (la frescura se mide por datos, no
por versión). Tras una promoción manual, re-materializar solo el precómputo:

```bash
dagster asset materialize --select "forecast_precomputado" \
  --partition "$(date +%F)" -m data_pipeline.orchestration.definitions
```
