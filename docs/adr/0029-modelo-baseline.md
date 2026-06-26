# Título: ADR-029: Modelo baseline determinista para el forecast de producción
**Estado:** Propuesta

## Contexto

El ADR-028 fija el encuadre del problema (regresión global, target `prod_pet` del mes siguiente, métrica MAE/RMSE, split temporal). Antes de entrenar cualquier modelo de Machine Learning, hay que establecer un **baseline**: un predictor **simple y determinista** contra el cual comparar.

El baseline cumple dos funciones:
1. **Vara de éxito:** un modelo de ML solo se justifica si **supera** al baseline. Si una regresión o un modelo de árboles no le gana a una regla trivial, no aporta valor y agrega complejidad innecesaria.
2. **Sentido de negocio:** la regla baseline debe ser interpretable por un operador, no una caja negra. Representa "qué pasaría si no hubiera modelo".

El EDA (`notebooks/02_outliers_correlaciones.ipynb`) ya mostró que la producción es **fuertemente autocorrelacionada**: el valor del mes actual correlaciona **0,95** con el del mes siguiente, y la media móvil de 3 meses **0,90**. Esto anticipa que una regla autoregresiva simple será un baseline difícil de superar.

## Análisis de Alternativas

Se evaluaron reglas deterministas, todas calculadas sobre el universo petrolero y medidas en el conjunto de **test** del ADR-028 (meses 2024-12 a 2026-04; 63.330 ejemplos; media del target ≈ 641 m³):

| Baseline | Regla | MAE (m³) | RMSE (m³) |
|---|---|---|---|
| Media global | ŷ = media de producción en train | 675,9 | 1351,6 |
| Naive estacional | ŷ(t+1) = y(t−11) (mismo mes, año anterior) | 354,9 | 954,2 |
| Media móvil 3m | ŷ(t+1) = promedio de los últimos 3 meses | 193,6 | 556,2 |
| **Persistencia (naive)** | **ŷ(t+1) = y(t) (último valor observado)** | **176,4** | **546,4** |

- **Media global:** ignora por completo la historia del pozo. Sirve solo como cota inferior de calidad (cualquier cosa razonable debe ganarle). Es el peor.
- **Naive estacional:** captura estacionalidad pero **ignora la declinación** del pozo, por lo que en pozos que caen fuerte sobreestima. Rinde peor que las reglas basadas en meses recientes.
- **Media móvil 3m:** suaviza el ruido; muy competitiva.
- **Persistencia:** la más simple posible y la **mejor**. Coherente con la alta autocorrelación observada.
- **Regla de declinación (Arps / tasa de declinación):** considerada como alternativa "de negocio" más sofisticada (ŷ = y(t)·(1 − tasa)). Se descartó **como baseline** porque requiere estimar una tasa por pozo, lo que ya la convierte en un mini-modelo y le quita el rol de referencia trivial. Queda como posible feature/idea para el modelo, no como baseline.

## Decisión

Adoptar la **persistencia (naive forecast: ŷ(t+1) = y(t))** como **baseline primario**, reportando **media móvil 3m** y **naive estacional** como referencias secundarias.

- **Umbral de éxito:** el modelo de ML deberá **superar MAE = 176,4 m³** (y RMSE = 546,4 m³) en el conjunto de test para considerarse que aporta valor.
- **Interpretación de negocio:** la persistencia equivale al supuesto operativo por defecto — *"el pozo seguirá produciendo lo mismo que el último mes"*. Es el punto de comparación natural para cualquier decisión.
- **Implementación:** el baseline se calcula de forma determinista (sin entrenamiento) y se registra en MLflow como un "run" más, con las mismas métricas y el mismo split que los modelos, para comparación directa.

## Consecuencias

**Positivas:**
- Queda un **criterio objetivo y cuantificado** para decidir si un modelo aporta valor (MAE < 176,4).
- El baseline es **interpretable** y tiene sentido de negocio inmediato.
- Al loguearse en MLflow con el mismo split, la comparación modelo-vs-baseline es directa y reproducible (insumo para el video).

**Negativas / trade-offs:**
- La persistencia es un baseline **exigente** (MAE bajo): el margen de mejora del modelo de ML es acotado, y habrá que demostrar que la ganancia justifica la complejidad.
- Tiene un **sesgo conocido**: como los pozos declinan, la persistencia tiende a sobreestimar levemente el mes siguiente. Se acepta porque el objetivo del baseline es ser simple, no óptimo.
- El umbral depende del split y del universo definidos en el ADR-028; si esos cambian, hay que recalcular el baseline.

---

> Relacionado: ADR-028 (encuadre y validación). El **algoritmo** del modelo que intentará superar este baseline se documentará en un ADR aparte.
