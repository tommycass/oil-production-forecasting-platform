# Título: ADR-041: Estrategia de orquestación del reentrenamiento

**Estado:** Propuesta

> Decide **cómo se reentrena el modelo de forma recurrente, automática y reproducible por fecha**. Reutiliza la orquestación de la Fase 2 ([ADR-011](0011-orquestador.md) Dagster, [ADR-018](0018-orquestacion-end-to-end-dw.md) grafo DW, [ADR-021](0021-refresh-bronze-full-reload.md) refresh) y el feature store ([ADR-036](0036-feature-store.md)). El entrenamiento es de Rol 1 ([ADR-040](0040-modelo-produccion.md)) y el tracking/registro en MLflow de Rol 3 ([ADR-037](0037-mlflow-server.md)).

## Contexto

La Fase 3 exige un reentrenamiento **recurrente y automático**, con **reproceso por fecha** ("reentrenar como si fuera el día X"). El flujo a orquestar es:

    refrescar features → entrenar → registrar el run en MLflow

Ya existe Dagster orquestando el DW (job `dw_publish`), disparado **headless por cron del SO** (sin daemon, para ahorrar RAM; ADR-018). Hay que decidir (a) cómo se **parametriza por fecha** el retrain y (b) cuál es el **criterio de disparo**.

### Alternativas evaluadas

#### Parametrización por fecha

- **Partitions de Dagster (diarias) — elegida.** Cada corrida se asocia a una `partition_key` = fecha. Reprocesar "como si fuera el día X" es materializar esa partición; el backfill de un rango es seleccionar particiones. Dagster registra qué partición corrió (trazable y reproducible).
- **Parámetro de configuración ad-hoc (run config).** Pasar la fecha como config del run. Funciona, pero no deja el historial particionado ni el soporte de backfill que dan las partitions; reimplementaría lo que Dagster ya ofrece.

Se eligen **particiones diarias** porque la consigna pide reproceso "por día"; aunque los datos sean mensuales, la fecha de corte del entrenamiento es diaria (anti-leakage: usar solo datos ≤ día X).

#### Criterio de disparo

- **Schedule + Sensor de Dagster (con dagster-daemon) — elegida.**
  - **Schedule mensual** (cron `0 6 6 * *`, día 6): corre después del refresh mensual del DW (cron día 5, ADR-021), cuando ya hay features del mes nuevo. Cubre "recurrente y automático por calendario".
  - **Sensor por llegada de datos**: dispara cuando el feature store gana un período nuevo (`max(periodo)` cambia). Cubre "por llegada de nuevos datos".
- **Cron del SO headless** (como `dw_publish`). Más liviano (sin daemon), pero el schedule vive en crontab —no "en Dagster"— y no hay sensor natural por datos.
- **Solo manual.** No cumple "recurrente y automático".

**Trade-off asumido:** Schedule y Sensor requieren el **dagster-daemon** persistente, que consume RAM en la EC2 (t2.medium + swap). Se acepta porque es lo que pide la adenda ("schedule o sensor en Dagster") y la entrega es sin servicio live 24/7 obligatorio: el daemon se levanta para demostrar/operar el retrain (ver runbook ml-retrain).

## Decisión

Un job de Dagster **`retrain`**, particionado por día, que encadena dos assets:

1. **`features_refrescadas`** — **re-materializa el feature store** (ADR-036): corre el pipeline de features de `ml/` sobre el crudo de Bronze y reescribe **las dos tablas** del store, `features.feat_produccion_pozo_mensual` (petróleo) y `..._gas` (gas, ADR-042), vía `materializar_todos`. (No usa dbt: el store ya no es un modelo dbt sino una materialización Python.) **Es el único lugar donde se materializa el store** (se desacopló de `dw_publish` para que el refresh del DW no dependa de `ml/`); por eso el venv del daemon de retrain es el que necesita las deps de `ml/`.
2. **`modelo_reentrenado`** — ejecuta el entrenamiento y registra el run en MLflow **para los dos modelos** (petróleo y gas, ADR-042): corre `RETRAIN_CMD` una vez por target (`--target prod_pet` / `--target prod_gas`), cada uno con su experimento/modelo de MLflow.

Disparo: **`retrain_mensual`** (Schedule, día 6) + **`retrain_por_features_nuevas`** (Sensor sobre `max(periodo)` del feature store, con cursor). Ambos requieren el dagster-daemon.

> **Dos modelos (ADR-042).** El job reentrena ambos en la misma corrida: un solo refresh de features (las dos tablas) seguido de dos entrenamientos (uno por target). Es lo que pide el ADR-042 ("la orquestación del retrain debería reentrenar ambos modelos").

### Paso de entrenamiento (configurable, desacoplado de Rol 1/3)

El job invoca el comando de entrenamiento de forma **configurable** (`RETRAIN_CMD`), sin pisar la zona de Rol 1/3. `modelo_reentrenado` le agrega `--target <target>` por cada modelo (petróleo / gas, ADR-042):

- **Default `python -m ml.baseline`**: entrena/evalúa y **loguea el run en MLflow** (cadena completa demostrable end-to-end). Acepta `--target`.
- **`python -m ml.train`** entrena el campeón (Random Forest, ADR-040) pero **hoy NO loguea en MLflow** a propósito: el experiment tracking (1.4) y el model registry (1.5) son de **Rol 3** (handoff de MLflow; ADR-037). Cuando Rol 3 enchufe ese logging (p. ej. `ml.train --mlflow` o `log_run(pipe, info)`), se exporta `RETRAIN_CMD="python -m ml.train --mlflow"` (el job le suma `--target` igual).
- **`RETRAIN_ASOF`** (env) = `partition_key`: el entrenamiento debe usar solo datos ≤ esa fecha (anti-leakage).
- **`MLFLOW_TRACKING_URI`** (env) apunta al servidor MLflow de Rol 3 (ADR-037); el código lo respeta sin cambios. Cada target usa su propio experimento/modelo (`experiment_name(target)`).

> El venv del dagster-daemon necesita las dependencias de `ml/` (mlflow, scikit-learn, …) para correr el paso de entrenamiento. `ml/requirements.txt` **ya existe** en el repo (Rol 1); el daemon lo instala (ver runbook).

## Consecuencias

**Positivas:**
- Reentrenamiento **recurrente y automático** (Schedule) y reactivo a datos (Sensor), declarado **en Dagster** y versionado.
- **Reproceso por fecha** nativo vía partitions ("como si fuera el día X") + backfill.
- Reutiliza el feature store (materialización ADR-036) y el grafo de la Fase 2; el paso de train queda desacoplado vía `RETRAIN_CMD`.

**Negativas / trade-offs:**
- Requiere operar el **dagster-daemon** (RAM; mitigable con swap o levantándolo on-demand para la demo).
- El "registrar en MLflow" del campeón depende de que Rol 3 enchufe el logging en `train.py`; hasta entonces la cadena se demuestra con `ml.baseline` (que sí loguea).
- El Sensor consulta Postgres en cada tick; se acota con `minimum_interval_seconds` y tolera fallos de DB (skip, no rompe el daemon).

## Relación con otros ADRs

- **ADR-036:** el feature store (materialización) es el insumo; el job lo refresca antes de entrenar.
- **ADR-018/021:** el retrain corre después del refresh mensual del DW.
- **ADR-030/037/040:** el run/modelo se registran en MLflow (tracking de Rol 3); la promoción a `Production` (criterio ADR-040) la consume la API.
- **ADR-011:** mismo orquestador (Dagster); este ADR agrega el daemon para Schedule/Sensor.
