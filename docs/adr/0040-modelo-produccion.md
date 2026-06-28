# Título: ADR-040: Modelo campeón a producción y criterio de promoción

**Estado:** Propuesta

## Contexto

El ADR-034 fijó la **metodología** (regresión tabular, comparación en val con CV temporal) y, sobre la comparación **sin tunear**, anticipó a **XGBoost** como mejor candidato. Con el **tuning de hiperparámetros** (CV temporal, random search) los resultados **cambian**, así que hay que fijar formalmente **cuál modelo va a producción** y con qué **criterio se promueve**, en base a la evidencia tuneada del notebook `03_modeling.ipynb` (§4).

Acá se decide **qué** modelo va a producción y con qué **criterio se promueve**. La **mecánica** del registry (versionado, stages Staging→Production, recarga en la API) se documenta aparte (ADR-037 servidor MLflow, ADR-038 estrategia de serving).

## Análisis de Alternativas

Comparación **tuneada en val** (entrenando en train, CV temporal para elegir hiperparámetros; `test` intacto). Notebook `03_modeling.ipynb` §4.1–4.2:

| Modelo (tuneado) | train RMSE | val RMSE | val R² | gap train→val |
|---|---|---|---|---|
| **Random Forest** | 132,9 | **229,9** | **0,900** | grande |
| Ridge (L2) | 278,1 | 241,8 | 0,890 | nulo |
| XGBoost | 156,9 | 244,3 | 0,887 | grande |
| Persistencia (baseline) | 310,0 | 250,6 | 0,881 | — |

- **Random Forest:** el **mejor en val** (RMSE 229,9 / R² 0,900) y el que **más le gana a la persistencia**. Tiene el **gap train→val más grande** (133→230): sobreajusta más, pero aun así **generaliza mejor** que el resto en val. Hiperparámetros ganadores: `n_estimators=400`, `max_depth=16`, `max_features=0.5`, `min_samples_leaf=2`.
- **Ridge:** segundo en val y el más interpretable; **no sobreajusta** (val incluso mejor que train, gap nulo), pero lineal puro no captura interacciones y se queda corto frente a RF. `alpha≈1129` (regularización fuerte).
- **XGBoost:** tercero. Era el mejor **sin tunear**, pero **tuneado lo superan RF y Ridge** (con los grids actuales); con `learning_rate=0,01` y árboles profundos queda parejo a Ridge en val y con gap train→val grande.
- **Persistencia:** el piso a batir (ADR-029); los tres modelos la superan.

### Confirmación en test

El campeón se entrenó en **dev (train+val, n=271.498)** con esos hiperparámetros y se evaluó **una sola vez en test** (`python -m ml.train --final`):

| | RMSE | R² |
|---|---|---|
| dev (train+val) | 137,8 | 0,969 |
| **test** | **154,4** | **0,874** |
| persistencia (test) | 166,2 | 0,854 |

**Supera a la persistencia en test** (RMSE 154 vs 166; R² 0,874 vs 0,854). El RMSE absoluto de test es menor que el de val porque el período de test tiene producciones de menor magnitud (la persistencia también baja); el R² (~0,87) es el comparable y se mantiene. El gap dev→test (R² 0,969→0,874) confirma algo de sobreajuste, pero el modelo generaliza y bate el baseline.

## Decisión

1. **Modelo campeón a producción: Random Forest tuneado**, por tener el **mejor RMSE/R² en val** y superar a la persistencia (**confirmado en test**: RMSE 154,4 / R² 0,874 vs persistencia 166,2 / 0,854). Hiperparámetros: `n_estimators=400, max_depth=16, max_features=0.5, min_samples_leaf=2`.
2. **Criterio de promoción (Staging→Production):** un modelo se promueve solo si **supera a la persistencia en RMSE en val** y lo **confirma en `test`** (evaluación única, al promover). Entre modelos candidatos, gana el de **menor val RMSE**.
3. **El campeón no es fijo:** se **re-evalúa en cada reentreno**. Los grids de tuning son chicos (ADR-034); con grids más amplios **XGBoost podría alcanzar o superar a RF**. La decisión la dicta la métrica en cada corrida, no este ADR de forma permanente.
4. **Alineación de código:** `ml/train.py` entrena por defecto el campeón (`random_forest`) **tuneado**.

Este ADR **refina la elección preliminar del ADR-034** (XGBoost sobre números sin tunear → **Random Forest** sobre números tuneados).

## Consecuencias

**Positivas:**
- Decisión **respaldada por evidencia** tuneada en val, con criterio de promoción **objetivo** y reproducible.
- Encadena con el registry/serving: el campeón es lo que se marca `Production` y la API sirve.

**Negativas / trade-offs:**
- Random Forest es **más pesado para servir** que XGBoost o un lineal (400 árboles, profundidad hasta 16 → más memoria/latencia); hay que tenerlo en cuenta al desplegar.
- El **gap train→val** de RF indica sobreajuste: hay margen para más regularización (más `min_samples_leaf`, `max_depth` más acotado) en próximos reentrenos.
- La **confirmación en `test`** ya se ejecutó una vez (RMSE 154,4 / R² 0,874, supera la persistencia); en sucesivos reentrenos, test debe seguir usándose con moderación para no "ajustar a test".
- Que el campeón pueda cambiar entre reentrenos exige que la **comparación esté automatizada** (encaja con el job de retrain).

---

> Relacionados: **ADR-034** (algoritmo y validación temporal, que este ADR refina), **ADR-029** (baseline / vara de éxito), **ADR-039** (preprocesamiento), **ADR-030** (MLflow), **ADR-037** (servidor MLflow) y **ADR-038** (serving), donde se implementan el registry y la promoción.
