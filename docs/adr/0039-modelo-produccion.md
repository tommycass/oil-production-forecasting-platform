# Título: ADR-039: Modelos de producción (petróleo y gas), campeones y criterio de promoción

**Estado:** Aceptada

## Contexto

La plataforma pronostica **dos** producciones (requisito confirmado por la cátedra, Discord 26–29/06/2026: *"ambas sería la idea"*): **petróleo** (`prod_pet`) y **gas** (`prod_gas`), cada una para el **mes siguiente (t+1)** con grano **(pozo, mes)**. Se entrena **un modelo por target**. Este ADR decide, para los dos targets: (a) que sean **dos modelos independientes** (no multi-salida), (b) **qué algoritmo es campeón** y (c) el **criterio de promoción** a producción.

El ADR-034 fijó la **metodología** (regresión tabular, comparación en val con CV temporal) y, sin tunear, anticipó a **XGBoost**. Con el **tuning** de hiperparámetros los resultados cambian, así que hay que fijar formalmente el campeón. Se mantiene **un único ADR para los dos modelos** —en vez de duplicar el análisis— porque comparten encuadre, metodología, criterio y código (pipeline parametrizado por `target`). El modelo se entrena con el set de features de la selección (ADR-041), recursion-safe, para que el **mismo** campeón sirva a la predicción de un mes y al **forecast recursivo** de `/forecast` (ADR-042). La **mecánica** del registry (versionado, stages, recarga en la API) se documenta aparte (ADR-036 servidor MLflow, ADR-037 serving).

## Análisis de Alternativas

### 1. Dos modelos vs. un modelo multi-salida

- **Un único modelo multi-salida** (`prod_pet` y `prod_gas` a la vez): comparte representación, pero **mezcla dos targets de escala y comportamiento distintos**, complica métricas/baseline/promoción y obliga a un único criterio de campeón para dos fenómenos.
- **Dos modelos independientes (elegido):** un modelo por target, cada uno con su baseline, su métrica, su campeón y su versión en el registry. Comparten el **mismo encuadre** (regresión tabular global, horizonte t+1, grano (pozo, mes)) y el **mismo código** parametrizado por `target`, así que no hay dos bases de código.

### 2. Algoritmo campeón (comparación tuneada en val)

Comparación **tuneada en val** (entrenando en train, CV temporal para elegir hiperparámetros; `test` intacto), por target. Notebooks `02_feature_selection_pet.ipynb` (petróleo) y `03_feature_selection_gas.ipynb` (gas).

**Petróleo (`prod_pet`):**

| Modelo (tuneado) | val RMSE | val R² |
|---|---|---|
| **Random Forest** | **227,1** | **0,903** |
| XGBoost | 236,9 | 0,894 |
| Ridge (L2) | 239,0 | 0,892 |
| Persistencia (baseline) | 250,6 | 0,881 |

**Gas (`prod_gas`):**

| Modelo (tuneado) | val RMSE | val R² |
|---|---|---|
| **Random Forest** | **580,4** | **0,859** |
| XGBoost | 580,5 | 0,859 |
| Ridge (L2) | 591,4 | 0,854 |
| Persistencia (baseline) | 635,5 | 0,831 |

**Lectura (vale para ambos targets):**
- **Random Forest** es el **mejor en val** y el que **más le gana a la persistencia** en los dos. Sobreajusta (train RMSE < val), pero **generaliza mejor** que el resto en val.
- **XGBoost** era el mejor **sin tunear**, pero **tuneado lo supera Random Forest**. Queda segundo, muy cerca de RF en gas.
- **Ridge** no sobreajusta pero, lineal puro, no captura interacciones y se queda corto.
- **Persistencia**: el piso a batir (ADR-029); los tres modelos la superan en val en los dos targets.

> **Hiperparámetros por target:** tras el tuning, **RF difiere** entre targets (petróleo 200 árboles / prof. 24; gas 400 / 16; ambos `min_samples_leaf=5`). **XGBoost coincidió** entre petróleo y gas (mismo grid + semilla del random search, ADR-034) y el `alpha` de Ridge es data-dependiente. Ver `ml/modeling.py::BEST_PARAMS`.

### Confirmación en test

Cada campeón se entrenó en **dev (train+val)** con sus hiperparámetros y se evaluó **una sola vez en test** (`python -m ml.train --target ... --final`), sobre el set de features de la selección (ADR-041):

| Target | dev RMSE | dev R² | **test RMSE** | **test R²** | persistencia test (RMSE / R²) |
|---|---|---|---|---|---|
| **Petróleo** (n_dev 271.498) | 160,1 | 0,959 | **157,5** | **0,869** | 166,2 / 0,854 |
| **Gas** (n_dev 309.996) | 375,4 | 0,943 | **409,2** | **0,850** | 457,8 / 0,813 |

**Ambos superan a la persistencia en test.** El RMSE absoluto de test es menor que el de val porque el período de test tiene producciones de menor magnitud (la persistencia también baja); el **R²** es el comparable y se mantiene (~0,87 petróleo, ~0,85 gas). El margen sobre la persistencia se erosiona algo en test (petróleo ~5%, gas ~11% en RMSE), coherente con lo que anticipa el ADR-041: fuera de val el *edge* del modelo se achica, pero los dos generalizan y baten su baseline.

## Decisión

1. **Dos modelos independientes, uno por target** (no multi-salida): mismo encuadre (regresión tabular global, t+1, grano (pozo, mes)) y **mismo código parametrizado por `target`**; cada modelo se entrena, evalúa, versiona y promueve por separado.
2. **Campeón por target: Random Forest tuneado en ambos**, por mejor RMSE/R² en val y superar a la persistencia (**confirmado en test**). Hiperparámetros: petróleo `n_estimators=200, max_depth=24, max_features=0.5, min_samples_leaf=5`; gas `n_estimators=400, max_depth=16, max_features=0.5, min_samples_leaf=5`.
3. **Universo de cada target (train-only, anti-leakage):** pozos con ese target `> 0` en al menos un mes, definido **solo con `train`** (`periodo <= TRAIN_END`). El gasífero es más amplio (`prod_gas` está en 79% de los meses vs 64% de `prod_pet`). Excluye ceros estructurales (inyección/sumidero) sin usar la etiqueta `tipopozo`.
4. **Criterio de promoción (Staging→Production), compartido y por separado por target:** un modelo se promueve solo si **supera a la persistencia en RMSE en val** y lo **confirma en `test`** (evaluación única, al promover). Entre candidatos, gana el de **menor val RMSE**.
5. **El campeón no es fijo:** se **re-evalúa en cada reentreno**. Los grids son chicos (ADR-034); con grids más amplios XGBoost podría alcanzar a RF. La métrica de cada corrida decide, no este ADR de forma permanente.
6. **Alineación de código:** los mejores hiperparámetros viven en `ml/modeling.py::BEST_PARAMS[target]`; `python -m ml.train --target {prod_pet|prod_gas}` entrena el campeón tuneado y `--final` confirma en test.

Este ADR **refina la elección preliminar del ADR-034** (XGBoost sobre números sin tunear → **Random Forest** sobre números tuneados), para los dos targets.

### Reuso del pipeline y anti-leakage (gas y petróleo)

El modelo de gas **no duplica código**: se **parametriza el target** del pipeline de `ml/` (`config.py`, `dataset.py`, `features.py`, `modeling.py`, `baseline.py`). El de gas reutiliza tal cual las familias de modelos (ADR-034), el baseline (ADR-029, persistencia sobre gas), el split temporal y la CV (ADR-028/034), el preprocesamiento (ADR-038) y el one-hot con `DESCONOCIDO` (ADR-032). Las features de ingeniería se **espejan sobre el target** (`prod_gas_*` en vez de `prod_pet_*`), con el mismo mecanismo de lag por calendario (ADR-033) → misma garantía anti-leakage. Mantener `prod_pet(t)` como feature del modelo de gas es válido (es una medida del mes `t`, no del futuro). La protección anti-leakage es **temporal** (features en `t`, target en `t+1`; estadísticos por fold; universo solo-train) y **no depende de qué variable sea el target**.

## Gestión de los dos modelos

- **Un solo código, parametrizado por `target`:** el mismo pipeline (`ml/`) sirve a los dos; el target elige universo, columna objetivo y features de ingeniería.
- **Un experimento MLflow y un modelo de registry por target:** `produccion-forecast` (petróleo) y `produccion-forecast-gas` (gas), para no mezclar runs ni versiones. Cada uno se promueve a `Production` por su cuenta.
- **Reentrenamiento (ADR-040):** el job de retrain reentrena **ambos** en la misma corrida (una por target).
- **Serving (ADR-037/042):** la API devuelve las dos predicciones vía `/forecast` con un parámetro `target` opcional (`prod_pet` por defecto, `prod_gas`).

## Consecuencias

**Positivas:**
- Cubre **los dos targets** con evidencia tuneada en val y confirmada en test, y un criterio de promoción **objetivo, reproducible y único**.
- **Sin duplicación:** un ADR, un pipeline y un criterio cubren ambos modelos; agregar un tercer target reusa todo (sumarlo a `ml.config.TARGETS`).
- Parametrizar el target **deja el pipeline más general** y elimina el `prod_pet` hardcodeado.
- Encadena con el registry/serving: el campeón de cada target es lo que se marca `Production` y la API sirve.

**Negativas / trade-offs:**
- Random Forest es **más pesado para servir** que XGBoost o un lineal (200–400 árboles según target → más memoria/latencia), y son **dos** modelos RF. Si pesara, XGBoost (segundo, muy cerca en val) es la alternativa liviana.
- El **gap train→val** de RF indica sobreajuste en ambos: margen para más regularización en próximos reentrenos.
- La **confirmación en `test`** ya se ejecutó una vez por target; en sucesivos reentrenos, test debe usarse con moderación para no "ajustar a test".
- `water_cut` (agua/(agua+petróleo)) aporta menos al gas que al petróleo; se evalúa en el notebook.
- Mantener **dos campeones** exige comparación/promoción **automatizada** para los dos (encaja con el job de retrain, ADR-040).

---

> Relacionados: **ADR-028** (encuadre del problema: dos targets, grano y métrica), **ADR-034** (algoritmo y validación temporal, que este ADR refina), **ADR-029** (baseline / vara de éxito), **ADR-031/033** (dataset y feature engineering parametrizados), **ADR-032/038** (encoding y preprocesamiento), **ADR-041** (selección de features: set de cada modelo), **ADR-030/036** (tracking y servidor MLflow), **ADR-037** (serving), **ADR-042** (`/forecast`: expone el target con el parámetro `target`) y **ADR-040** (retrain, reentrena ambos modelos).
