# Título: ADR-045: Precómputo del forecast en el retrain (serving por lookup, transparente)

**Estado:** Aceptada

## Contexto

`/forecast` (ADR-044) corre el motor recursivo **en cada request**: lee la historia del pozo
del feature store, carga el modelo Production y encadena hasta 12 predicciones. Funciona y
cumple el RNF de latencia (~0,6 s el peor caso), pero tiene costos estructurales:

- **El modelo está en el request path:** si MLflow no está disponible o no hay versión
  Production cargada, el endpoint devuelve 503 aunque los datos estén sanos.
- **Cómputo repetido:** los datos cambian **una vez por mes** (ingesta mensual, ADR-018) y el
  modelo cambia **una vez por retrain** (mensual, ADR-041). Entre medio, cada request al mismo
  pozo recalcula exactamente el mismo resultado.
- **Latencia atada al recompute:** ~45 ms por paso recursivo por pozo; aceptable, pero no es
  un lookup.

Como el pronóstico es **determinístico** dadas (features del store, modelo Production), se
puede calcular **una vez por ciclo** — para todos los pozos, a 12 meses (el horizonte máximo
del endpoint) — y servirlo por lookup. La restricción de diseño es que sea **transparente**:
el contrato y los valores de `/forecast` no deben cambiar para el usuario.

## Análisis de Alternativas

### 1. Estrategia de serving

- **On-demand puro (status quo, descartado):** simple y sin estado extra, pero recalcula lo
  mismo en cada request y mantiene a MLflow en el camino crítico del endpoint.
- **Precómputo puro (descartado):** la API solo lee la tabla precomputada. Máxima simpleza en
  el request, pero crea una **ventana de inconsistencia**: entre que el DW/store se refresca
  y el precómputo corre, las predicciones son viejas respecto de los datos — y si el batch
  falla, el endpoint queda ciego (sin camino alternativo).
- **Híbrido: precómputo + fallback on-the-fly (elegido):** la API sirve el precómputo **solo
  si está fresco** (su `ultimo_observado` coincide con el `max(periodo)` del store para ese
  pozo); si no hay precómputo, quedó viejo o está incompleto para la ventana pedida, corre el
  motor recursivo como siempre. Nunca se sirve un dato stale y ninguna falla del batch
  degrada el endpoint (a lo sumo pierde el ahorro).

### 2. Dónde se genera el precómputo

- **En la ingesta mensual (`dw_publish`) (descartado):** en el momento de la ingesta el
  modelo nuevo **todavía no existe** (el retrain corre después, día 6 vs día 5) → precomputaría
  con el modelo viejo y habría que recalcular igual tras el retrain. Además acoplaría el
  refresh del DW (Fase 2) a las deps de `ml/` (sklearn/mlflow), que es exactamente lo que la
  revisión del ADR-036 desacopló.
- **Job/schedule propio (descartado):** duplica orquestación (otro schedule, otro sensor)
  para algo que tiene un disparador natural; suma superficie de fallas y coordinación.
- **Como paso final del job de retrain (elegido):** el asset `forecast_precomputado` corre
  después de `features_refrescadas → modelo_reentrenado` — el único momento en que hay
  **store fresco + modelo Production recién evaluado/promovido**. Hereda el schedule mensual
  y el sensor del retrain (ADR-041) sin orquestación nueva.

### 3. Cómo garantizar que el precómputo = on-the-fly (transparencia)

- **Reimplementar el batch aparte (descartado):** cualquier divergencia (features, orden de
  pasos, redondeo) rompería la transparencia silenciosamente.
- **Reusar el mismo motor (elegido):** el batch llama a `ml.forecast.recursive_forecast` —
  la **misma función** que usa la API — con el mismo modelo Production del registry y las
  mismas filas del store. Mismo código + mismos inputs ⇒ mismos valores. Las validaciones
  de rango (422) y de universo (404) también salen del mismo dato (`ultimo_observado`) por
  ambos caminos.

### 4. Detección de staleness

- **TTL / timestamp de generación (descartado):** un umbral temporal no dice si los datos
  cambiaron; puede servir stale dentro del TTL o recalcular sin necesidad.
- **Comparar `ultimo_observado` contra el store por pozo (elegido):** el precómputo guarda,
  por fila, desde qué último mes observado se generó; la API lo compara con el
  `max(periodo)` actual del store para ese pozo (un `SELECT max` barato). Coinciden → el
  precómputo refleja exactamente los datos vigentes; difieren → fallback. Es una condición
  **exacta**, no heurística.

## Decisión

1. **Nuevo asset `forecast_precomputado`** (Dagster, grupo retrain, ADR-041): tras reentrenar,
   corre `ml.forecast.recursive_forecast` para **cada pozo** del store con el modelo
   **Production** de cada target y escribe `features.pred_produccion_pozo_mensual`
   (petróleo) y `..._gas` (gas): 12 filas por pozo (`idpozo`, `periodo` pronosticado,
   `prediccion`, `ultimo_observado`, `model_name`, `model_version`, `generado_en`).
   - Horizonte = **12 meses** = `MAX_FORECAST_MONTHS` del endpoint (ADR-044): el precómputo
     cubre todo lo que la API puede llegar a servir.
   - Si un target **no tiene modelo en Production**, se saltea (warning + metadata); la API
     sigue por on-the-fly. Un pozo cuyo forecast falla se saltea sin voltear el batch.
   - **No honra `RETRAIN_ASOF`**: el precómputo es para servir *hoy* (siempre store completo
     + Production vigente), no un artefacto histórico → re-correrlo en un backfill es
     idempotente e inofensivo.
2. **La API sirve el precómputo como lookup, con fallback:** `get_forecast` intenta primero
   la tabla precomputada; la usa solo si (a) hay filas para el pozo, (b) están **frescas**
   (`ultimo_observado` == `max(periodo)` del store) y (c) cubren **completa** la ventana
   pedida. Si no, camino on-the-fly de siempre (ADR-044). El **contrato no cambia** en nada:
   mismos params, misma respuesta, mismos códigos (404/422 salen de las mismas reglas y el
   503 solo puede ocurrir en el fallback).
3. El contrato de las tablas queda documentado en `docs/feature-store.md` (sección
   *Pronóstico precomputado*).

## Consecuencias

**Positivas:**
- `/forecast` con precómputo fresco es un **SELECT + recorte**: latencia de milisegundos,
  **sin MLflow ni modelo en el request path** (si MLflow se cae, el endpoint sigue sirviendo
  mientras el precómputo esté fresco — antes era 503).
- El cómputo pasa de *por request* a **una vez por ciclo mensual**, alineado con la cadencia
  real de los datos. Costo del batch acotado y fuera de línea (~0,6 s por pozo en el peor
  caso, serial; corre dentro del job de retrain).
- Transparente por construcción: mismo motor, mismo modelo, mismas features ⇒ mismos valores;
  el usuario no puede distinguir qué camino respondió.
- La tabla precomputada queda disponible para BI/monitoreo (pronóstico de todos los pozos en
  una tabla del DW, con versión de modelo y fecha de generación).

**Negativas / trade-offs:**
- **Estado adicional**: dos tablas más que mantener (aunque las regenera el retrain de punta
  a punta, `if_exists="replace"`).
- El batch **alarga el job de retrain** (~minutos a decenas de minutos sobre el universo
  real, serial). Mitigable a futuro vectorizando por paso (predecir todos los pozos juntos
  en cada mes) sin cambiar el contrato.
- Si el DW se refresca y el retrain aún no corrió, el precómputo queda stale → la API cae al
  on-the-fly (correcto pero sin ahorro) hasta el próximo retrain. Es el comportamiento
  deseado: consistencia antes que ahorro.
- Una promoción de modelo **fuera del ciclo** (manual) deja el precómputo calculado con el
  Production anterior; como la frescura se mide por datos (no por versión de modelo), se
  serviría hasta el próximo retrain. Operativamente: tras promover a mano, re-materializar
  `forecast_precomputado` (runbook ml-retrain §7).

---

> Relacionados: **ADR-044** (motor recursivo que este ADR precomputa), **ADR-041** (job de
> retrain que lo orquesta), **ADR-043**/**ADR-046** (set de features de la selección), **ADR-036** (feature store,
> fuente de las features y de la señal de frescura), **ADR-040** (criterio de promoción del
> modelo Production que consume el batch). Implementación:
> `data_pipeline/orchestration/forecast_precompute.py` (batch),
> `data_pipeline/orchestration/retrain.py` (asset), `api/app/services/forecast.py` +
> `feature_reader.py` (lookup + fallback). Contrato: `docs/feature-store.md`.
