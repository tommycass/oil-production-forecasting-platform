# Título: ADR-040: Modelos campeones a producción (petróleo y gas) y criterio de promoción

**Estado:** Propuesta

## Contexto

El ADR-034 fijó la **metodología** (regresión tabular, comparación en val con CV temporal) y, sobre la comparación **sin tunear**, anticipó a **XGBoost** como mejor candidato. Con el **tuning de hiperparámetros** (CV temporal, random search) los resultados **cambian**, así que hay que fijar formalmente **cuál modelo va a producción** y con qué **criterio se promueve**.

La plataforma pronostica **dos** producciones (decisión de la cátedra, ADR-042): **petróleo** (`prod_pet`) y **gas** (`prod_gas`), cada una con su modelo. Este ADR decide **el campeón de cada target** y el **criterio de promoción** (compartido), en base a la evidencia tuneada de los notebooks `03_modeling.ipynb` (petróleo) y `04_modeling_gas.ipynb` (gas). Se mantiene **un único ADR** para los dos modelos —en vez de duplicar el análisis— porque comparten metodología, criterio y código (pipeline parametrizado por target, ADR-042).

La **mecánica** del registry (versionado, stages Staging→Production, recarga en la API) se documenta aparte (ADR-037 servidor MLflow, ADR-038 estrategia de serving).

## Análisis de Alternativas

Comparación **tuneada en val** (entrenando en train, CV temporal para elegir hiperparámetros; `test` intacto), por target.

### Petróleo (`prod_pet`) — notebook `03_modeling.ipynb` §4.1–4.2

| Modelo (tuneado) | train RMSE | val RMSE | val R² | gap train→val |
|---|---|---|---|---|
| **Random Forest** | 132,9 | **229,9** | **0,900** | grande |
| Ridge (L2) | 278,1 | 241,8 | 0,890 | nulo |
| XGBoost | 156,9 | 244,3 | 0,887 | grande |
| Persistencia (baseline) | 310,0 | 250,6 | 0,881 | — |

### Gas (`prod_gas`) — notebook `04_modeling_gas.ipynb` §4.1–4.2

| Modelo (tuneado) | train RMSE | val RMSE | val R² | gap train→val |
|---|---|---|---|---|
| **Random Forest** | 257,8 | **579,9** | **0,859** | grande |
| XGBoost | 297,8 | 587,2 | 0,856 | grande |
| Ridge (L2) | 538,7 | 611,5 | 0,844 | chico |
| Persistencia (baseline) | 580,7 | 635,5 | 0,831 | — |

**Lectura (vale para ambos targets):**
- **Random Forest** es el **mejor en val** y el que **más le gana a la persistencia** en los dos. Tiene el **gap train→val más grande** (sobreajusta), pero aun así **generaliza mejor** que el resto en val.
- **XGBoost** era el mejor **sin tunear** en ambos, pero **tuneado lo supera Random Forest** (con los grids actuales). Queda segundo (petróleo: tercero, parejo a Ridge).
- **Ridge** es el más interpretable y **no sobreajusta** (gap chico/nulo), pero lineal puro no captura interacciones y se queda corto frente a RF.
- **Persistencia**: el piso a batir (ADR-029); los tres modelos la superan en val, en los dos targets.

> **Coincidencia de hiperparámetros:** RF y XGBoost ganaron con los **mismos** hiperparámetros en petróleo y gas (mismo grid + misma semilla del random search, ADR-034). Solo difiere el `alpha` de Ridge (petróleo ≈1129, gas ≈29,8), que es data-dependiente. Por eso los `BEST_PARAMS` por target casi se solapan.

### Confirmación en test

Cada campeón se entrenó en **dev (train+val)** con sus hiperparámetros y se evaluó **una sola vez en test** (`python -m ml.train [--target ...] --final`):

| Target | dev RMSE | dev R² | **test RMSE** | **test R²** | persistencia test (RMSE / R²) |
|---|---|---|---|---|---|
| **Petróleo** (n_dev 271.498) | 137,8 | 0,969 | **154,4** | **0,874** | 166,2 / 0,854 |
| **Gas** (n_dev 309.996) | 273,8 | 0,969 | **401,0** | **0,856** | 457,8 / 0,813 |

**Ambos superan a la persistencia en test.** El RMSE absoluto de test es menor que el de val porque el período de test tiene producciones de menor magnitud (la persistencia también baja); el **R²** es el comparable y se mantiene (~0,87 petróleo, ~0,86 gas). El gap dev→test confirma algo de sobreajuste, pero los dos modelos generalizan y baten su baseline.

## Decisión

1. **Campeón por target: Random Forest tuneado en ambos**, por tener el **mejor RMSE/R² en val** y superar a la persistencia (**confirmado en test**). Hiperparámetros (idénticos en los dos): `n_estimators=400, max_depth=16, max_features=0.5, min_samples_leaf=2`.
2. **Criterio de promoción (Staging→Production), compartido y aplicado por separado a cada target:** un modelo se promueve solo si **supera a la persistencia en RMSE en val** y lo **confirma en `test`** (evaluación única, al promover). Entre candidatos, gana el de **menor val RMSE**.
3. **El campeón no es fijo:** se **re-evalúa en cada reentreno**. Los grids de tuning son chicos (ADR-034); con grids más amplios **XGBoost podría alcanzar o superar a RF**. La decisión la dicta la métrica en cada corrida, no este ADR de forma permanente.
4. **Alineación de código:** los mejores hiperparámetros viven en `ml/modeling.py::BEST_PARAMS[target]`; `python -m ml.train --target {prod_pet|prod_gas}` entrena el campeón (`random_forest`) tuneado y `--final` confirma en test.

Este ADR **refina la elección preliminar del ADR-034** (XGBoost sobre números sin tunear → **Random Forest** sobre números tuneados), para los dos targets.

## Gestión de los dos modelos

Cómo conviven petróleo y gas sin duplicar trabajo:

- **Un solo código, parametrizado por `target`** (ADR-042): el mismo pipeline (`ml/`: dataset, features, preprocesamiento, modelos, baseline, train) sirve a los dos; el target elige universo, columna objetivo y features de ingeniería. No hay dos bases de código.
- **Un experimento MLflow y un modelo de registry por target:** `produccion-forecast` (petróleo) y `produccion-forecast-gas` (gas), para no mezclar runs ni versiones. Cada uno se promueve a `Production` por su cuenta con el criterio de arriba.
- **Reentrenamiento (ADR-041):** el job de retrain debe correr **ambos** modelos (queda como extensión del job, que hoy contempla un target).
- **Serving (ADR-035/038):** la API debe poder devolver las dos predicciones — exponer **ambos** modelos o agregar un parámetro `target` en `/predict` (a definir con Rol 3).

## Consecuencias

**Positivas:**
- Decisión **respaldada por evidencia** tuneada en val y confirmada en test, con un criterio de promoción **objetivo, reproducible y único** para los dos targets.
- **Sin duplicación:** un ADR, un pipeline y un criterio cubren ambos modelos; agregar un tercer target en el futuro reusa todo.
- Encadena con el registry/serving: el campeón de cada target es lo que se marca `Production` y la API sirve.

**Negativas / trade-offs:**
- Random Forest es **más pesado para servir** que XGBoost o un lineal (400 árboles, profundidad 16 → más memoria/latencia), y ahora son **dos** modelos RF; a tener en cuenta al desplegar. Si pesara, XGBoost (segundo, muy cerca en val) es la alternativa más liviana.
- El **gap train→val** de RF indica sobreajuste en ambos: margen para más regularización (más `min_samples_leaf`, `max_depth` más acotado) en próximos reentrenos.
- La **confirmación en `test`** ya se ejecutó una vez por target; en sucesivos reentrenos, test debe usarse con moderación para no "ajustar a test".
- Mantener **dos campeones** exige que la comparación/promoción esté **automatizada** para los dos (encaja con el job de retrain).

---

> Relacionados: **ADR-034** (algoritmo y validación temporal, que este ADR refina), **ADR-042** (segundo modelo de gas: diseño, universo y anti-leakage), **ADR-029** (baseline / vara de éxito), **ADR-039** (preprocesamiento), **ADR-030** (MLflow), **ADR-037** (servidor MLflow), **ADR-038** (serving) y **ADR-041** (retrain, a extender a ambos modelos).
