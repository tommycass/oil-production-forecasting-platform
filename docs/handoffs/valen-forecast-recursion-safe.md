# Handoff → Valen: feature store al set **recursion-safe** (35 features)

> **Estado (jul-2026): COMPLETADO — y superado.** El store se re-materializa con el retrain
> y quedó al **set de ganancia positiva (27 petróleo / 19 gas)**: se implementó la Decisión del
> ADR-043 revisada por [ADR-046](0046-seleccion-features-ganancia-positiva.md)
> (`ml.features.selected_features`), no el candidato de 35. La fila del
> último mes con `y_next` NULL se conserva (left-join) y la paridad training↔store quedó
> validada e2e. Contrato vigente: `docs/feature-store.md`. Además el retrain ahora deja el
> pronóstico **precomputado** (ADR-045). Lo de abajo queda como registro histórico del pedido.

**De:** Micol (ML) · **Contexto:** rediseño del forecast a **recursivo** (ADR-043/044). Esto toca el feature store que venís manejando.

## Qué cambió (y por qué)

El modelo pasó a ser **recursion-safe**: entrena solo con las features que se pueden **recalcular hacia el futuro** (autorregresivas del target + estáticas), para poder hacer el forecast recursivo de `/forecast` (predecir t+1, realimentar y seguir). En consecuencia, **el feature store ahora materializa solo esas 35 features** (antes 40).

Cambio concreto en `data_pipeline/orchestration/feature_store_build.py` (`build_store_features`): se filtra la lista de columnas al set recursion-safe. Se **dejan de materializar**: `prod_vecinos_mean`, `water_cut`, `prod_agua`, `tef` y la **producción cruzada** (el otro target). Cada tabla queda con **35 features + 3 claves + `y_next` = 39 columnas** (antes 44).

- 35 features = **5 numéricas** (`{target}` nivel del mes t + `profundidad` + `coordenadax/y` + `mes`) + **16 de ingeniería** (autorregresivas `{target}_roll3/roll6/delta1/delta3/ratio1/lag2/lag3/lag12/acum6/acum12/std3/cummax/frac_peak/meses_desde_pico` + `produjo_mes_pasado` + `well_age_months`) + **14 categóricas**.

## Qué tenés que hacer (acción)

1. **Re-materializar el store** para que las tablas reflejen las 35 columnas: correr el asset `features_refrescadas` (retrain) o `materializar_todos(engine)` directo. Sin esto, las tablas viejas quedan con las 40 columnas de antes.
2. **Verificar** que las dos tablas (`feat_produccion_pozo_mensual` y `..._gas`) tengan las 39 columnas y que un pozo con historia tenga:
   - la **fila del último mes con `y_next` NULL** — es la que habilita la inferencia del mes siguiente. Ya lo hacía el `left-join` en `build_store_features`; **confirmá que sigue** (es crítico para `/forecast`).
   - las columnas autorregresivas recursion-safe (`{target}_roll3/roll6/lag2/.../meses_desde_pico`).

## Cómo lo consume la inferencia (contexto, ya implementado)

`/forecast` (recursivo) lee del store vía `api/app/services/feature_reader.py::get_history_for_forecast`:
- la **fila del mes base** (`SELECT *` del último mes del pozo, descartando `idpozo/periodo/periodo_objetivo/y_next`) → predice el primer mes **sin recalcular** (usa tus features pre-computadas);
- la **serie** `(periodo, <target>)` de todos los meses → recalcula on-the-fly **solo las autorregresivas** de los meses futuros (t+2+, que no existen en el store).

Ese reader ya está escrito; solo necesita que las tablas estén **re-materializadas** con las 35 columnas.

## Referencias
- Contrato actualizado: `docs/feature-store.md` (35 features / 39 columnas).
- ADRs: **043** (selección recursion-safe), **044** (forecast recursivo), **036** (feature store).
- Código: `data_pipeline/orchestration/feature_store_build.py`.
