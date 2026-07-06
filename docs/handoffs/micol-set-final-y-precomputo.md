# Handoff → Micol: set final de features implementado (ADR-041) + precómputo del forecast (ADR-043)

**De:** Valen (Rol 2) · **Contexto:** sobre tu rama del forecast recursivo, implementé la **Decisión de tu ADR-041** de punta a punta y sumé el precómputo del forecast. También arreglé un par de cosas que quedaron colgadas en la rama.

## 1. El set FINAL de la selección quedó implementado (antes: candidato de 35)

Tu ADR-041 decidía un set de **14 features** (Capa 1 autorregresiva + Capa 2 anclas), pero el código entrenaba con el candidato recursion-safe completo (35, incluida `delta3` que tu ranking descarta por importancia negativa). Ahora las tres puntas usan la Decisión:

- **Fuente única:** `ml.features.selected_features(target)` (las 14 "Mantener" de tu tabla; borderline y descartadas afuera — el ADR-041 quedó actualizado marcando las borderline como podadas por parsimonia).
- `ml/train.py` entrena con `build_feature_matrix(target, selected=True)` (nuevo flag; `recursion_safe=True` sigue existiendo para el ranking de tus notebooks 02/03).
- El **feature store** materializa exactamente esas columnas (18 por tabla: 3 claves + 14 + `y_next`). Contrato: `docs/feature-store.md`.
- `recursion_safe_cols`/`NON_RECURSION_SAFE` se movieron a `ml/features.py` (se re-exportan desde `ml.modeling` para que tus notebooks no se rompan).

## 2. Qué te queda a vos (cuando puedas, con datos reales)

1. **Re-tunear `BEST_PARAMS` sobre el set final** (hoy quedaron los del candidato de 35 — siguen siendo un punto de partida razonable y tu §4 mostró que el set compacto rinde ~3% peor en val con esos params). Refrescar los números de ADR-039/README (sobre todo **gas**, que no tiene medición del set compacto).
2. Si una borderline vuelve a justificarse, agregala a `selected_features` y listo: store y serving se re-materializan solos en el próximo retrain.

> **Respuesta (Micol, jul-2026) — resuelto:** revisé la Decisión del ADR-041 y en vez del set compacto de 14 adopté el **set de ganancia positiva** (todas las features con `imp_mean > 0` del ranking): **27 en petróleo, 19 en gas** ([ADR-041](0041-seleccion-features-forecast.md)). Esto además responde tu punto 2: las borderline que el 043 había podado por parsimonia (`std3`, `frac_peak`, `lag12`, `lag3`, `meses_desde_pico`, `mes`, `areapermisoconcesion`, `tipopozo`, `empresa`, …) vuelven a entrar porque su ganancia es positiva.
>
> **Números refrescados** (ADR-039 / ADR-041 / README), val de los notebooks + **test** corrido con `--final` sobre el set nuevo — los dos targets, gas incluido:
> - Petróleo (27): val RMSE 227,5 / R² 0,902 · **test 157,5 / 0,869** · persistencia 166,2 / 0,854.
> - Gas (19): val RMSE 576,6 / R² 0,861 · **test 409,2 / 0,850** · persistencia 457,8 / 0,813.
> - Los dos superan la persistencia en val y test. En petróleo el set nuevo **mejora** el test del compacto (157,5 vs 164,7); en gas queda igual.
>
> **Sobre re-tunear `BEST_PARAMS`: decidí NO hacerlo, a propósito.** El tuneo (notebooks 02/03) se corrió sobre el candidato de **35**, y las 27/19 son un **subconjunto** de ese — o sea los params ya se eligieron sobre un espacio que contiene al set final, y el campeón con esos mismos params ya se validó sobre el recorte en **val** (cell 21) y en **test** (`--final`), superando la persistencia. Re-tunear ahora se apartaría de la propia metodología del 043 (tunear sobre 35, evaluar el recorte). La única sensibilidad real al tamaño del set es `max_features=0.5` (una **fracción**: ~17 features/split con 35, ~13 con 27), pero su efecto ya queda absorbido por esa validación empírica. Los params vigentes se mantienen: petróleo `n_estimators=200, max_depth=24, max_features=0.5, min_samples_leaf=5`; gas `400/16/0.5/5`.

## 3. Fixes que venían de tu rama (ya resueltos, FYI)

- `data_pipeline/tests/test_feature_store_build.py` esperaba las columnas viejas → actualizado al set final (+ test de que las descartadas NO se persisten).
- **CI roto:** el job `test-ml` corría `pytest api/tests/test_predict.py`, que tu rama borra → ahora corre `api/tests/test_forecast.py` + `ml/tests/test_forecast.py` (que no estaba cableado) + los tests del store/precómputo (que en `test-pipeline` se saltean por no tener sklearn).
- Ejemplo del schema de `/forecast` seguía con fechas diarias → mensual.

## 4. Nuevo: precómputo del forecast (ADR-043) — no te pide nada

El job de retrain ganó un tercer asset (`forecast_precomputado`): corre **tu motor** (`ml.forecast.recursive_forecast`) para todos los pozos con el modelo Production y deja `features.pred_produccion_pozo_mensual` (+`_gas`); la API lo sirve como lookup con fallback al on-the-fly. Cero cambios en `ml/` para esto (solo reuso).

## Referencias
ADRs: **043** (actualizado: Decisión implementada), **040** (Revisión jul-2026), **045** (nuevo), **041** (Revisión). Código: `ml/features.py` (`selected_features`), `ml/train.py`, `data_pipeline/orchestration/feature_store_build.py`, `forecast_precompute.py`.
