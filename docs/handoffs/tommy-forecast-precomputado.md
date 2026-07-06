# Handoff → Tommy: /forecast con precómputo (ADR-043) + set final de features — revisar y re-promover

**De:** Valen (Rol 2) · **Contexto:** toqué serving (tu zona) para que `/forecast` sirva el pronóstico **precomputado** que deja el retrain, con fallback al motor de siempre. El contrato NO cambia. También cambió el set de features del modelo (ADR-041) → hay que **re-entrenar y re-promover** después del merge.

## Update (jul-2026, Valen) — estado post-merge + 2 PRs abiertos

Revisé la cadena completa con el set de ganancia positiva (27 petróleo / 19 gas) ya en `staging`:

1. **Sigue pendiente tu acción del §2:** re-entrenar y **re-promover ambos targets** con el set 27/19 (`ml.train --mlflow --target prod_pet` y `--target prod_gas`, o el job `retrain` con `RETRAIN_CMD="python -m ml.train --mlflow"`) contra el MLflow del deploy. Confirmá si ya lo corriste: un `Production` viejo contra el store 27/19 recibe columnas que no coinciden → **predicción degradada en silencio**.
2. **PR `fix/pin-grpcio-health-checking`** — mergealo **antes** de rebuildear el daemon (`infra/Dockerfile.dagster`) o de que corra el CI `test-pipeline`: `grpcio-health-checking 1.82` (dep transitiva de dagster) trae gencode de protobuf 7.35, que rompe el import de `dagster_dbt` contra el `protobuf<7` que exigen dbt/dagster. El PR capea `grpcio`/`grpcio-health-checking <1.82` (validado: `data_pipeline/tests` 35 passed).
3. **Cadena validada e2e** (Postgres + MLflow): `materializar_todos` → `train --mlflow` (ambos targets promueven, superan persistencia) → `precomputar_todos` → `GET /forecast` **200** con puntos mensuales + 404/422/403 OK. Tu paso post-merge es de bajo riesgo. El PR `fix/feature-store-mes-objetivo` alinea el `mes` del store al mes objetivo (t+1): **transparente** para tu reader (lo excluye de `STATIC_FEATURE_COLUMNS` y el motor lo re-setea por paso), sin acción de tu lado.

## 1. Qué cambió en la API (revisá estos archivos)

- `api/app/services/forecast.py`: `get_forecast` intenta primero el **lookup precomputado**
  (`features.pred_produccion_pozo_mensual` / `_gas`) y solo si no hay precómputo fresco corre
  el motor recursivo como antes. La lógica de ventana/horizonte quedó en un helper `_ventana`
  compartido por ambos caminos (mismos 422). Sin precómputo el comportamiento es EXACTAMENTE
  el de antes.
- `api/app/services/feature_reader.py`: + `get_precomputed_forecast` y
  `get_ultimo_periodo_observado` (chequeo de frescura por pozo). `STATIC_FEATURE_COLUMNS`
  quedó en las 4 anclas del set final (`areayacimiento`, `profundidad`, `coordenadax/y`).
- Tests: `api/tests/test_forecast.py` suma 5 casos del precómputo (lookup fresco sin tocar
  el modelo, ventana/horizonte, 422, stale→fallback, sin tabla→fallback). Los 16 tuyos
  quedaron igual y pasan.
- **Beneficio operativo:** con precómputo fresco, MLflow sale del request path — si el server
  MLflow se cae, `/forecast` sigue respondiendo (el 503 solo puede darse en el fallback).

## 2. ⚠️ Acción post-merge: re-entrenar y RE-PROMOVER ambos targets

El modelo pasó a entrenarse con el **set de ganancia positiva** (27 petróleo / 19 gas,
[ADR-041](0041-seleccion-features-forecast.md)) y el store ahora
materializa **solo esas columnas**. Un modelo viejo en Production (entrenado con otro set)
frente al store nuevo recibiría NaN/DESCONOCIDO en las columnas que no coinciden → **predicción
degradada en silencio**. Después del merge, en el entorno con MLflow:

```bash
python -m ml.train --mlflow --target prod_pet
python -m ml.train --mlflow --target prod_gas
```

(o correr el job `retrain` con `RETRAIN_CMD="python -m ml.train --mlflow"`, que además
re-materializa el store y deja el precómputo listo — es el camino recomendado, ver runbook
ml-retrain §4). La promoción usa tu criterio del ADR-039; contra un Production viejo con
`test_rmse` taggeado puede requerir `--no-promote` + promoción manual si la comparación
histórica no aplica (sets distintos) — criterio tuyo.

## 3. Verificación end-to-end sugerida

1. Retrain completo (`features_refrescadas → modelo_reentrenado → forecast_precomputado`).
2. `SELECT count(*) FROM features.pred_produccion_pozo_mensual;` → 12 filas por pozo.
3. `GET /api/v1/forecast?...` de un pozo → 200, contrato de Fase 1.
4. Transparencia: `DROP TABLE features.pred_produccion_pozo_mensual;` y repetir la request →
   misma respuesta (vía motor). Volver a materializar `forecast_precomputado`.

## Referencias
ADRs: **043** (precómputo, alternativas), **042** (motor), **041** (set final), **040** (job).
Contrato de tablas: `docs/feature-store.md` §Pronóstico precomputado. Runbook: `docs/runbooks/ml-retrain.md` §6–7.
