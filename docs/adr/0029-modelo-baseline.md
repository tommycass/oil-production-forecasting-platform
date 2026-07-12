# Título: ADR-029: Modelo baseline determinista para el forecast de producción
**Estado:** Aceptada

## Contexto

El ADR-028 fija el encuadre del problema (regresión global, target `prod_pet` del mes siguiente, métrica MAE/RMSE, split temporal). Antes de entrenar cualquier modelo de Machine Learning, hay que establecer un **baseline**: un predictor **simple y determinista** contra el cual comparar.

El baseline cumple dos funciones:
1. **Vara de éxito:** un modelo de ML solo se justifica si **supera** al baseline. Si una regresión o un modelo de árboles no le gana a una regla trivial, no aporta valor y agrega complejidad innecesaria.
2. **Sentido de negocio:** la regla baseline debe ser interpretable por un operador, no una caja negra. Representa "qué pasaría si no hubiera modelo".

El EDA (`notebooks/01_outliers_correlaciones.ipynb`) ya mostró que la producción es **fuertemente autocorrelacionada**: el valor del mes actual correlaciona **0,95** con el del mes siguiente, y la media móvil de 3 meses **0,90**. Esto anticipa que una regla autoregresiva simple será un baseline difícil de superar.

## Análisis de Alternativas

Se evaluaron reglas deterministas sobre el **dataset unificado** (`build_basic_dataset`: universo petrolero train-only + target por merge de calendario, ADR-031), las mismas que comen los modelos. Se miden en **val** (comparación directa con los modelos, ADR-034) y en **test** (vara de éxito final). Cifras de `python -m ml.baseline`:

| Baseline | Regla | val MAE | val RMSE | test MAE | test RMSE |
|---|---|---|---|---|---|
| Naive estacional | ŷ(t+1) = prod_pet(t−11) (mismo mes, año anterior) | 280,4 | 778,2 | 153,0 | 399,4 |
| Media móvil 3m | ŷ(t+1) = media de {t, t−1, t−2} | 103,5 | 311,6 | 64,3 | 190,3 |
| **Persistencia (naive)** | **ŷ(t+1) = prod_pet(t)** | **81,8** | **250,6** | **53,4** | **165,2** |

(unidades en m³; n ≈ 47.900 en val y 51.000 en test.)

- **Media global** (ŷ = media de train): ignora por completo la historia del pozo; cota inferior trivial de calidad. No se incluye en la tabla por estar muy lejos de las demás.
- **Naive estacional:** captura estacionalidad pero **ignora la declinación** del pozo (sobreestima en pozos que caen) y pierde cobertura (necesita 12 meses de historia). Es el peor de los tres.
- **Media móvil 3m:** suaviza el ruido, pero al promediar 3 meses **se rezaga** frente a la persistencia en una serie tan autocorrelacionada.
- **Persistencia:** la más simple posible y la **mejor** en val y test. Coherente con la alta autocorrelación observada (corr 0,95 con el mes siguiente).
- **Regla de declinación (Arps / tasa de declinación):** alternativa "de negocio" más sofisticada (ŷ = y(t)·(1 − tasa)). Se descartó **como baseline** porque requiere estimar una tasa por pozo, lo que ya la convierte en un mini-modelo y le quita el rol de referencia trivial. Queda como posible feature/idea para el modelo, no como baseline.

> La persistencia en val (RMSE 250,6 / R² 0,881) coincide exactamente con la que reporta el notebook `02_feature_selection_pet.ipynb`, confirmando que baselines y modelos corren sobre el mismo dataset y split.

## Decisión

Adoptar la **persistencia (naive forecast: ŷ(t+1) = y(t))** como **baseline primario**, reportando **media móvil 3m** y **naive estacional** como referencias secundarias.

- **Umbral de éxito:** el modelo de ML deberá **superar a la persistencia en RMSE en val** (≈ 250,6 m³ sobre el dataset unificado) y confirmarlo en test. La métrica primaria de comparación es **RMSE** (ADR-034); las cifras exactas del baseline se obtienen con `python -m ml.baseline`.
- **Interpretación de negocio:** la persistencia equivale al supuesto operativo por defecto — *"el pozo seguirá produciendo lo mismo que el último mes"*. Es el punto de comparación natural para cualquier decisión.
- **Implementación:** el baseline se calcula de forma determinista (sin entrenamiento) y se registra en MLflow como un "run" más, con las mismas métricas y el mismo split que los modelos, para comparación directa. La misma regla de persistencia se aplica a **ambos targets** (`prod_pet` y `prod_gas`): `ml.baseline` está parametrizado por target, así que el modelo de gas (ADR-039) tiene su propia persistencia sobre `prod_gas` como vara.

## Consecuencias

**Positivas:**
- Queda un **criterio objetivo** para decidir si un modelo aporta valor (superar a la persistencia en RMSE sobre val/test).
- El baseline es **interpretable** y tiene sentido de negocio inmediato.
- Al loguearse en MLflow con el mismo split, la comparación modelo-vs-baseline es directa y reproducible (insumo para el video).

**Negativas / trade-offs:**
- La persistencia es un baseline **exigente** (MAE bajo): el margen de mejora del modelo de ML es acotado, y habrá que demostrar que la ganancia justifica la complejidad.
- Tiene un **sesgo conocido**: como los pozos declinan, la persistencia tiende a sobreestimar levemente el mes siguiente. Se acepta porque el objetivo del baseline es ser simple, no óptimo.
- El umbral depende del split y del universo definidos en el ADR-028; si esos cambian, hay que recalcular el baseline.

---

> Relacionado: ADR-028 (encuadre y validación), ADR-031/033 (dataset y features), ADR-030 (MLflow). El **algoritmo** del modelo que intenta superar este baseline se documenta en **ADR-034**.
