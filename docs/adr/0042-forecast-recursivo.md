# Título: ADR-042: Forecast recursivo multi-paso (endpoint `/forecast` sobre el modelo mensual)

**Estado:** Aceptada

## Contexto

`/forecast` existe desde la Fase 1: recibe un **rango de fechas** (`date_start`, `date_end`) y devolvía un mock de **declinación lineal diaria** (`get_forecast`, sin modelo). El objetivo es que deje de ser un mock y produzca un **pronóstico real** sobre un rango, usando el modelo de ML.

El modelo de ML predice **un mes hacia adelante** (t+1) — el **paso unitario**. Un rango se cubre encadenando predicciones: **forecast recursivo (multi-paso)** = predecir t+1, tratar esa predicción como si fuera dato observado, recalcular las features y predecir t+2, y así sucesivamente. Ese paso unitario (predecir un solo mes) es un endpoint aparte innecesario: un `/forecast` con rango de un mes lo cubre, así que `/forecast` queda como **único endpoint de pronóstico**.

Esto es viable **gracias al set de features recursion-safe** (ADR-041): las features que quedaron (autorregresivas del propio target + estáticas + calendario) se pueden **recalcular en cada paso** desde la trayectoria del target. Las que se excluyeron (`prod_vecinos_mean` cross-well, `water_cut`, `prod_gas`, `prod_agua`, `tef`) **no** se pueden recalcular hacia el futuro — por eso el forecast recursivo **depende de un modelo entrenado solo con features recursion-safe**.

## Análisis de Alternativas

### 1. Estrategia multi-paso: recursivo vs directo

- **Directo multi-horizonte (descartado por ahora):** entrenar un modelo por horizonte (t+1, t+2, … t+h) o uno que reciba el horizonte como input. No acumula error, pero multiplica el costo de entrenamiento/mantenimiento y no reusa el modelo de un paso ya existente.
- **Recursivo (elegido):** reusa **un único modelo de un paso** (la predicción mensual) realimentando la predicción. Aprovecha directamente el set recursion-safe (ADR-041). Trade-off: el **error se acumula** con el horizonte (cada predicción se apoya en la anterior) → se mitiga con un **horizonte máximo** acotado.

### 2. Granularidad de salida: mensual vs diaria

- **Diaria (descartada):** el mock devolvía un punto por día, pero el modelo **no** aprende dinámica diaria (los datos son mensuales, ADR-028/033). Repartir un valor mensual a días agrega un supuesto artificial que el modelo no respalda.
- **Mensual (elegida):** un valor de producción **por mes** del rango. Coincide con la granularidad del modelo. Las fechas del request se interpretan como un **rango de meses**.

### 3. Endpoint del paso unitario

- **Un endpoint separado para la predicción de un mes (descartado):** duplica lógica y superficie de API. Un `/forecast` con rango de un solo mes ya devuelve exactamente esa predicción.
- **Un solo endpoint de pronóstico (elegido):** `forecast` es un **loop sobre la predicción de un mes** (features → pipeline → valor), así que **subsume** al paso unitario. Queda `/forecast` como único endpoint. El parámetro `target` (petróleo/gas) es **opcional** en `/forecast` (default `prod_pet`), sin romper el contrato de Fase 1.

### 4. `date_start` anterior al último dato real

- **Devolver dato real + forecast (descartado):** mezcla en una misma respuesta valores observados y predichos.
- **Rechazar con 422 (descartado):** demasiado estricto para un caso legítimo (pedir un rango que arranca "antes").
- **Solo forecast a futuro (elegido):** el pronóstico arranca en el **primer mes sin dato real** (`max(date_start, último_mes_observado + 1)`); los meses ya observados del rango se ignoran. Un forecast es del futuro. Si el rango **no tiene ningún mes futuro** (todo el rango es pasado), se responde **422** ("no hay meses para pronosticar en el rango").

### 5. Horizonte máximo

- El rango se acota con un tope en **meses** (`MAX_FORECAST_MONTHS = 12`), medido **desde el último mes con dato del pozo** (es lo que acota la cantidad de pasos recursivos). Además de evitar respuestas enormes, **acota la acumulación de error**: un pronóstico recursivo a muchos meses es cada vez menos confiable.
- **Qué hacer si el rango pedido supera el tope: recortar (elegido) vs rechazar.** Se **recorta** hasta el mes máximo permitido y se devuelven los meses hasta ahí (responde "hasta donde sí"), en vez de rechazar toda la request. Como el contrato de respuesta de Fase 1 es fijo (`{id_well, data:[{date, prod}]}`), el recorte **no** lleva un campo `truncated`; se documenta en la descripción del endpoint. Solo se responde **422** si el rango **empieza** más allá del tope (no queda ningún mes dentro del horizonte para devolver).

## Decisión

Reemplazar el mock de `/forecast` por un **forecast recursivo mensual**, **conservando el contrato de Fase 1** (`id_well`, `date_start`, `date_end` → `{id_well, data:[{date, prod}]}`; se agrega solo el parámetro **opcional** `target`):

1. **Motor recursivo** (`ml/forecast.py`, función pura y testeable): recibe la fila de features del **mes base** (del store), la **serie mensual observada** y los **atributos estáticos**, más el **pipeline recursion-safe**; para cada mes futuro predice, **apenda la predicción a la serie**, **recalcula las features recursion-safe** (`ml.features`) y avanza. Devuelve la serie mensual `[(mes, producción)]`. La recursión **arranca en el mes siguiente al último dato del pozo** (`L+1`), que es lo que acota los pasos.

   **Estrategia de features (feature store en inferencia, RNF Fase 3):**
   - **Mes base → t+1:** se usan las **features pre-computadas del store** (la fila del último mes observado), **sin recalcular** — así el store se usa en inferencia (equivale a la predicción de un solo mes).
   - **Meses futuros → t+2, t+3, …:** se **recalculan** las features recursion-safe desde la serie extendida, porque esos meses **no existen** en el store (dependen de predicciones). Es inevitable en un forecast recursivo, y es **skew-free** porque usa las **mismas funciones de `ml/features.py`** con las que el store materializa (mismo código → mismo resultado; recalcular el mes base daría idéntico a su fila del store).
   - **Estáticas** (`profundidad`, coords, categóricas): vienen del store y **nunca se recalculan** (se replican).
2. **Un paso = el modelo de un paso** (el mismo que se registra en MLflow). El paso unitario no tiene endpoint propio: `/forecast` lo subsume (un rango de un mes = la predicción de un mes).
3. **Salida mensual**, **solo meses futuros** (arranca en `max(date_start, último_mes_observado + 1)`). Cada punto usa el campo `date` del contrato con la fecha del **primer día del mes**.
4. **Horizonte máximo en meses** (`MAX_FORECAST_MONTHS = 12`), medido desde el último dato del pozo; si el rango lo supera se **recorta** hasta ahí.
5. **Casos borde:**
   - `date_start > date_end` → 422.
   - `target` fuera de `{prod_pet, prod_gas}` → 422 (validado por tipo en el schema).
   - Rango sin meses futuros (todo el rango es pasado) → 422.
   - Rango que **empieza** más allá del horizonte máximo → 422; si solo el final lo supera, se **recorta** (200 con menos meses).
   - Pozo inexistente en el DW → 404; pozo sin serie en el feature store (fuera del universo, ADR-031) → 404.
   - Modelo no disponible en MLflow (inalcanzable o sin versión Production) → 503.
   - Cold-start / poca historia → las features autorregresivas quedan **0 + flag** (ADR-038); el modelo se apoya en las estáticas (ADR-041).
6. **Modelo:** el modelo de producción se entrena con el **set de la selección** (ADR-041, `selected_features`), recursion-safe por construcción: todas sus features se pueden recalcular hacia el futuro, así que `/forecast` recursa **directamente sobre el único modelo de producción**, sin un artefacto aparte.

## Consecuencias

**Positivas:**
- `/forecast` pasa a ser un pronóstico real multi-paso, con una sola familia de modelo (el de un paso) reutilizada, **sin cambiar el contrato de Fase 1** (`{id_well, data:[{date, prod}]}`).
- Aprovecha directamente el diseño recursion-safe (ADR-041): las features se recalculan solas en cada paso.
- Salida mensual coherente con la granularidad del modelo; sin supuestos diarios artificiales.
- **Un solo endpoint de pronóstico**: `/forecast` cubre también la predicción de un mes (un rango de un mes = una predicción), reduciendo la superficie de la API (menos código y tests que mantener).
- **Latencia dentro del RNF (< 5 s):** medido ~**0,6 s** en el peor caso (12 pasos sobre un pozo con ~20 años de historia); ~32 ms de recompute + ~13 ms de predict por paso. El horizonte máximo acota el costo. Hay un test de humo de regresión en `ml/tests/test_forecast.py`.
- Borde `date_start` en el pasado resuelto de forma simple (solo futuro).

**Negativas / trade-offs:**
- **Error acumulado:** cada paso se apoya en la predicción anterior; la confianza cae con el horizonte (por eso el tope en meses).
- **Necesita la serie histórica del pozo** (no solo la fila del mes base) para recalcular los pasos futuros. Se lee de la **misma tabla** que materializa el training (`feature_reader.get_history_for_forecast`); el store conserva la fila del último mes (left-join, `y_next` NULL) para habilitar la inferencia. Requiere que el store esté **re-materializado** con las columnas recursion-safe (lo hace el retrain, ADR-040).
- **Recorte silencioso:** al mantener el contrato fijo, cuando el rango supera el horizonte no hay un campo que lo señale (se documenta en la descripción del endpoint).
- Reproducir en serving el mismo cálculo de features que en training (paridad, ADR-035) es más delicado en modo recursivo (se recalcula paso a paso); se mitiga reusando las **mismas funciones** de `ml/features.py`.

---

> Relacionados: **ADR-041** (features recursion-safe: prerequisito del recursivo), **ADR-039** (modelo de producción, recursion-safe por defecto), **ADR-031/038** (universo y manejo de NaN/cold-start), **ADR-035** (feature store: fuente de la historia y paridad training-serving), **ADR-028/033** (granularidad mensual del modelo). Implementación: `ml/forecast.py` (motor), `api/app/services/forecast.py` y `api/app/routes/forecast.py` (endpoint).
