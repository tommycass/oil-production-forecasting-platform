# Título: ADR-044: Forecast recursivo multi-paso (endpoint `/forecast` sobre el modelo mensual)

**Estado:** Aceptada

## Contexto

Hay **dos endpoints** con roles distintos que conviene unificar:

- **`/predict`** (ADR-035): predice la producción de **un solo mes** (t+1) para un pozo. Es el **paso unitario**.
- **`/forecast`** (existe desde la Fase 1): recibe un **rango de fechas** (`date_start`, `date_end`) y hoy devuelve un mock de **declinación lineal diaria** (`get_forecast`, sin modelo).

El objetivo es que `/forecast` deje de ser un mock y produzca un **pronóstico real** sobre un rango, usando el modelo de ML. Como el modelo predice **un mes hacia adelante**, un rango se cubre encadenando predicciones: **forecast recursivo (multi-paso)** = predecir t+1, tratar esa predicción como si fuera dato observado, recalcular las features y predecir t+2, y así sucesivamente.

Esto es viable **gracias al set de features recursion-safe** (ADR-043): las features que quedaron (autorregresivas del propio target + estáticas + calendario) se pueden **recalcular en cada paso** desde la trayectoria del target. Las que se excluyeron (`prod_vecinos_mean` cross-well, `water_cut`, `prod_gas`, `prod_agua`, `tef`) **no** se pueden recalcular hacia el futuro — por eso el forecast recursivo **depende de un modelo entrenado solo con features recursion-safe**.

## Análisis de Alternativas

### 1. Estrategia multi-paso: recursivo vs directo

- **Directo multi-horizonte (descartado por ahora):** entrenar un modelo por horizonte (t+1, t+2, … t+h) o uno que reciba el horizonte como input. No acumula error, pero multiplica el costo de entrenamiento/mantenimiento y no reusa el modelo de un paso ya existente.
- **Recursivo (elegido):** reusa **un único modelo de un paso** (el de `/predict`) realimentando la predicción. Aprovecha directamente el set recursion-safe (ADR-043). Trade-off: el **error se acumula** con el horizonte (cada predicción se apoya en la anterior) → se mitiga con un **horizonte máximo** acotado.

### 2. Granularidad de salida: mensual vs diaria

- **Diaria (descartada):** el mock devolvía un punto por día, pero el modelo **no** aprende dinámica diaria (los datos son mensuales, ADR-028/033). Repartir un valor mensual a días agrega un supuesto artificial que el modelo no respalda.
- **Mensual (elegida):** un valor de producción **por mes** del rango. Coincide con la granularidad del modelo. Las fechas del request se interpretan como un **rango de meses**.

### 3. Relación con `/predict`

- **Reimplementar la predicción de un mes dentro de forecast (descartado):** duplica lógica.
- **Reusar el paso unitario (elegido):** `forecast` es un **loop sobre la predicción de un mes**. El código de predecir-un-mes (features → pipeline → valor) se factoriza y lo usan los dos. El equipo decide si `/predict` se **expone** en la API o queda como función interna; en cualquier caso el código se aprovecha.

### 4. `date_start` anterior al último dato real

- **Devolver dato real + forecast (descartado):** mezcla en una misma respuesta valores observados y predichos.
- **Rechazar con 422 (descartado):** demasiado estricto para un caso legítimo (pedir un rango que arranca "antes").
- **Solo forecast a futuro (elegido):** el pronóstico arranca en el **primer mes sin dato real** (`max(date_start, último_mes_observado + 1)`); los meses ya observados del rango se ignoran. Un forecast es del futuro. Si el rango **no tiene ningún mes futuro** (todo el rango es pasado), se responde **422** ("no hay meses para pronosticar en el rango").

### 5. Horizonte máximo

- El `/forecast` ya acota el rango (ADR previo: `MAX_FORECAST_DAYS`). Con la salida mensual, el tope pasa a **meses** (`MAX_FORECAST_MONTHS`, propuesto **12**). Además de evitar respuestas enormes, **acota la acumulación de error** del recursivo: un pronóstico recursivo a muchos meses es cada vez menos confiable.

## Decisión

Reemplazar el mock de `/forecast` por un **forecast recursivo mensual**:

1. **Motor recursivo** (`ml/forecast.py`, función pura y testeable): recibe la **serie mensual observada** del pozo (+ atributos estáticos) y el **pipeline recursion-safe**; para cada mes futuro predice, **apenda la predicción a la serie**, **recalcula las features recursion-safe** (`ml.features`) y avanza. Devuelve la serie mensual `[(mes, producción)]`.
2. **Un paso = el modelo de `/predict`** (reuso del código de predicción de un mes).
3. **Salida mensual**, **solo meses futuros** (arranca en `max(date_start, último_mes_observado + 1)`).
4. **Horizonte máximo en meses** (`MAX_FORECAST_MONTHS`).
5. **Casos borde:**
   - `date_start > date_end` → 422 (ya implementado).
   - Rango sin meses futuros → 422.
   - Horizonte > `MAX_FORECAST_MONTHS` → 422.
   - Pozo sin fila en el feature store (fuera del universo, ADR-031) → 404.
   - Cold-start / poca historia → las features autorregresivas quedan **0 + flag** (ADR-039); el modelo se apoya en las estáticas (ADR-043).
6. **Modelo:** el modelo de producción es **recursion-safe por defecto** (ADR-043/040): se entrena únicamente con features que se pueden recalcular hacia el futuro, así que `/forecast` recursa **directamente sobre el mismo modelo que sirve `/predict`**, sin un artefacto aparte.

## Consecuencias

**Positivas:**
- `/forecast` pasa a ser un pronóstico real multi-paso, con una sola familia de modelo (el de un paso) reutilizada.
- Aprovecha directamente el diseño recursion-safe (ADR-043): las features se recalculan solas en cada paso.
- Salida mensual coherente con la granularidad del modelo; sin supuestos diarios artificiales.
- Borde `date_start` en el pasado resuelto de forma simple (solo futuro).

**Negativas / trade-offs:**
- **Error acumulado:** cada paso se apoya en la predicción anterior; la confianza cae con el horizonte (por eso el tope en meses).
- **Necesita la serie histórica del pozo** para sembrar la recursión (no solo la fila del feature store): hay que leerla del store/DW.
- Reproducir en serving el mismo cálculo de features que en training (paridad, ADR-036) es más delicado en modo recursivo (se recalcula paso a paso).

---

> Relacionados: **ADR-035** (contrato de `/predict`, el paso unitario), **ADR-043** (features recursion-safe: prerequisito del recursivo), **ADR-040** (modelo de producción, recursion-safe por defecto), **ADR-031/039** (universo y manejo de NaN/cold-start), **ADR-036** (feature store: fuente de la historia y paridad training-serving), **ADR-028/033** (granularidad mensual del modelo). Implementación: `ml/forecast.py` (motor), `api/app/services/forecast.py` y `api/app/routes/forecast.py` (endpoint).
