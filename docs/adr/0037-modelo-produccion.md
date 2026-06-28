# Título: ADR-037: Modelo campeón a producción y criterio de promoción

**Estado:** Propuesta

## Contexto

El ADR-034 fijó la **metodología** (regresión tabular, comparación en val con CV temporal) y, sobre la comparación **sin tunear**, anticipó a **XGBoost** como mejor candidato. Con el **tuning de hiperparámetros** (CV temporal, grid search) los resultados **cambian**, así que hay que fijar formalmente **cuál modelo va a producción** y con qué **criterio se promueve**, en base a la evidencia tuneada del notebook `03_modeling.ipynb` (§4).

Esta es la tarea **1.5** del Rol 1 (definir el modelo campeón y el criterio de promoción). La **mecánica** del registry (versionado, stages Staging→Production, recarga en la API) la implementa el **Rol 3** (ADR-038 servidor MLflow, ADR-039 estrategia de serving); acá se decide **qué** se promueve y **cuándo**.

## Análisis de Alternativas

Comparación **tuneada en val** (entrenando en train, CV temporal para elegir hiperparámetros; `test` intacto). Notebook `03_modeling.ipynb` §4.1–4.2:

| Modelo (tuneado) | train RMSE | val RMSE | val R² | gap train→val |
|---|---|---|---|---|
| **Random Forest** | 164,4 | **226,8** | **0,903** | grande |
| XGBoost | 208,8 | 240,5 | 0,891 | medio |
| Ridge (L2) | 282,8 | 244,2 | 0,887 | chico |
| Persistencia (baseline) | 310,0 | 250,6 | 0,881 | — |

- **Random Forest:** el **mejor en val** (RMSE 226,8 / R² 0,903) y el que **más le gana a la persistencia**. Tiene el **gap train→val más grande** (164→227): sobreajusta más, pero aun así **generaliza mejor** que el resto en val. Hiperparámetros ganadores: `n_estimators=300`, `max_depth=None`, `max_features=0.3`, `min_samples_leaf=5`.
- **XGBoost:** segundo. Es el que ganaba **sin tunear**, pero **tuneado lo supera Random Forest** (con los grids actuales). Menos sobreajuste que RF.
- **Ridge:** el más interpretable y el de menor gap (no sobreajusta), pero el peor de los modelos; lineal puro no captura interacciones.
- **Persistencia:** el piso a batir (ADR-029); los tres modelos la superan.

## Decisión

1. **Modelo campeón a producción: Random Forest tuneado**, por tener el **mejor RMSE/R² en val** y superar a la persistencia. Hiperparámetros: `n_estimators=300, max_depth=None, max_features=0.3, min_samples_leaf=5`.
2. **Criterio de promoción (Staging→Production):** un modelo se promueve solo si **supera a la persistencia en RMSE en val** y lo **confirma en `test`** (evaluación única, al promover). Entre modelos candidatos, gana el de **menor val RMSE**.
3. **El campeón no es fijo:** se **re-evalúa en cada reentreno**. Los grids de tuning son chicos (ADR-034); con grids más amplios **XGBoost podría alcanzar o superar a RF**. La decisión la dicta la métrica en cada corrida, no este ADR de forma permanente.
4. **Alineación de código:** `ml/train.py` entrena por defecto el campeón (`random_forest`) **tuneado**.

Este ADR **refina la elección preliminar del ADR-034** (XGBoost sobre números sin tunear → **Random Forest** sobre números tuneados).

## Consecuencias

**Positivas:**
- Decisión **respaldada por evidencia** tuneada en val, con criterio de promoción **objetivo** y reproducible.
- Encadena con el registry/serving del Rol 3: el campeón es lo que se marca `Production` y la API sirve.

**Negativas / trade-offs:**
- Random Forest es **más pesado para servir** que XGBoost o un lineal (300 árboles sin límite de profundidad → más memoria/latencia); el Rol 3 debe tenerlo en cuenta al desplegar.
- El **gap train→val** de RF indica sobreajuste: hay margen para más regularización (más `min_samples_leaf`, `max_depth` acotado) en próximos reentrenos.
- La **confirmación en `test`** todavía no se ejecutó (se hace una sola vez al promover); el número de val es la base de la decisión, no el definitivo.
- Que el campeón pueda cambiar entre reentrenos exige que la **comparación esté automatizada** (encaja con el job de retrain del Rol 2).

---

> Relacionados: **ADR-034** (algoritmo y validación temporal, que este ADR refina), **ADR-029** (baseline / vara de éxito), **ADR-036** (preprocesamiento), **ADR-030** (MLflow), **ADR-038** (servidor MLflow) y **ADR-039** (serving), donde el Rol 3 implementa el registry y la promoción.
