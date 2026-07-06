# Título: ADR-046: Revisión de la selección — set de ganancia positiva (imp > 0)

**Estado:** Aceptada (jul-2026) — revisa la Decisión del [ADR-043](0043-seleccion-features-forecast.md)

## Contexto

El [ADR-043](0043-seleccion-features-forecast.md) rankeó las 35 features candidatas recursion-safe por **permutation importance en val** (notebooks `02_feature_selection_pet.ipynb` y `03_feature_selection_gas.ipynb`) y, sobre ese ranking, fijó un **set compacto de 14 features** podando por **parsimonia** todas las *borderline* — features con importancia **positiva pero chica** (`std3`, `frac_peak`, `lag12`, `lag3`, `meses_desde_pico`, `mes`, `areapermisoconcesion`, `tipopozo`, `empresa`, y las categóricas `tipoextraccion`, `proyecto`, `cuenca`). Ese recorte aceptaba **ceder ~3% de RMSE en val** (RF 234,5 vs 227,8 del candidato de 35) a cambio de menos columnas que materializar.

Al revisar el resultado surge una objeción: el criterio de poda es **subjetivo**. El umbral "aporte < 1,5 m³ → afuera" no sale de los datos; es una preferencia por parsimonia. Y su costo no es gratis: se está tirando señal que el propio ranking marcó como **útil** (importancia > 0 = permutar esa feature **empeora** el RMSE de val). El `ml.selection.select_features` ya expone el corte natural, no subjetivo: **quedarse con todas las features de ganancia positiva** (`imp_mean > 0`).

Este ADR adopta ese criterio: reemplazar el recorte manual a 14 por el **set de ganancia positiva** que devuelve la selección.

## Análisis de Alternativas

### 1. Criterio de corte sobre el ranking

- **Set compacto de 14, poda por parsimonia (ADR-043, revertido):** menos columnas, pero descarta features de importancia positiva y el umbral es arbitrario. Cede ~3% de val RMSE en petróleo.
- **Ganancia positiva `imp_mean > 0` (elegido):** corte **empírico y no subjetivo** — se conserva toda feature que reduce el RMSE de val. Da 27 features en petróleo y 19 en gas. Recupera el ~3% cedido (y en gas **mejora** al candidato completo, ver métricas).
- **Estricto `imp_mean > imp_std` (descartado por ahora):** dejaría solo las que superan su propio ruido. Es defendible (algunas categóricas conservadas tienen imp ≈ 0,00–0,05, dentro de su `imp_std`), pero vuelve a introducir un umbral de decisión; se deja anotado como corte alternativo re-evaluable.

### 2. Costo de las categóricas conservadas

- Con el set de ganancia positiva entran categóricas antes descartadas (`empresa` —alta cardinalidad—, `tipopozo`, `areapermisoconcesion`, `proyecto`, `cuenca` en petróleo; `tipoestado`, `clasificacion`, `sub_tipo_recurso`, `provincia`, `formprod` en gas). Inflan el one-hot, pero el costo de materialización es **trivial** al volumen del store (~6 k filas por tabla, ADR-036) y el `Pipeline` ya maneja categorías nuevas con `DESCONOCIDO` (ADR-032). El *edge* es que dan **más señal estática al cold-start** (un pozo nuevo suma tipo de pozo, empresa y área a las anclas que ya tenía).

### 3. Impacto en el forecast recursivo

- Todas las features de ganancia positiva son **subconjunto del candidato recursion-safe de 35**, así que **ninguna** rompe el forecast (ADR-044): las autorregresivas se recalculan desde la trayectoria del target y las estáticas/categóricas se replican por paso. Se verificó que el motor (`ml/forecast.py`) recalcula todas las autorregresivas del set y que `api/app/services/feature_reader.py` arrastra las categóricas estáticas nuevas (`STATIC_FEATURE_COLUMNS`) para los pasos t+2+.

## Decisión

Fijar el set del modelo en las **features de ganancia positiva** (`imp_mean > 0` del ranking del ADR-043), **por target**:

**Petróleo (`prod_pet`) — 27 features:**
`prod_pet`, `prod_pet_roll3`, `prod_pet_ratio1`, `prod_pet_acum12`, `prod_pet_roll6`, `prod_pet_acum6`, `prod_pet_cummax`, `prod_pet_lag2`, `prod_pet_delta1`, `prod_pet_std3`, `prod_pet_frac_peak`, `prod_pet_lag12`, `prod_pet_lag3`, `prod_pet_meses_desde_pico`, `produjo_mes_pasado`, `areayacimiento`, `profundidad`, `coordenadax`, `coordenaday`, `well_age_months`, `areapermisoconcesion`, `tipopozo`, `empresa`, `mes`, `tipoextraccion`, `proyecto`, `cuenca`.

**Gas (`prod_gas`) — 19 features:**
`prod_gas`, `prod_gas_roll3`, `prod_gas_roll6`, `prod_gas_acum12`, `prod_gas_acum6`, `prod_gas_ratio1`, `prod_gas_lag3`, `prod_gas_lag12`, `prod_gas_std3`, `prod_gas_cummax`, `prod_gas_delta3`, `prod_gas_lag2`, `tipoestado`, `mes`, `clasificacion`, `sub_tipo_recurso`, `provincia`, `formprod`, `tipoextraccion`.

**Quedan fuera** las features de importancia ≈ 0 o negativa (ruido): en petróleo `provincia`, `subclasificacion`, `clasificacion`, `tipoestado`, `sub_tipo_recurso`, `formacion`, `formprod` y `prod_pet_delta3` (imp. negativa); en gas, sus análogas y `prod_gas_delta1`. Se mantienen fuera del candidato, por recursion-safety, `prod_vecinos_mean`, `water_cut`, el target cruzado, `prod_agua` y `tef` (ADR-043).

**Implementación (fuente única de verdad):** el set vive en `ml.features.selected_features(target)`, que ahora devuelve listas **distintas por target** (petróleo y gas rankearon distinto). Lo consumen sin poder divergir el entrenamiento (`ml/train.py`), el feature store (`feature_store_build.py`, ADR-036) y el serving (`/forecast`, ADR-044/045). El retrain re-materializa el store con las nuevas columnas en la próxima corrida (ADR-041).

### Métricas (val, notebooks 02/03)

Los notebooks miden en **val** (test quedó intacto). Comparación de los 3 modelos tuneados sobre el candidato de 35 y del campeón (Random Forest) reajustado sobre el set de ganancia positiva:

**Petróleo (`prod_pet`):**

| Modelo / set | Features | val RMSE | val R² |
|---|---:|---:|---:|
| Random Forest (candidato) | 35 | 227,3 | 0,903 |
| **Random Forest (ganancia positiva)** | **27** | **227,5** | **0,902** |
| Random Forest (compacto, ADR-043) | 14 | 234,5 | 0,896 |
| XGBoost (candidato) | 35 | 236,9 | 0,894 |
| Ridge (candidato) | 35 | 239,0 | 0,892 |
| Persistencia (baseline) | — | 250,6 | 0,881 |

**Gas (`prod_gas`):**

| Modelo / set | Features | val RMSE | val R² |
|---|---:|---:|---:|
| **Random Forest (ganancia positiva)** | **19** | **576,6** | **0,861** |
| Random Forest (candidato) | 35 | 580,1 | 0,859 |
| XGBoost (candidato) | 35 | 580,5 | 0,859 |
| Ridge (candidato) | 35 | 591,4 | 0,854 |
| Persistencia (baseline) | — | 635,5 | 0,831 |

Lectura: en **petróleo** el set de ganancia positiva (27) recupera casi todo el ~3% que cedía el compacto (227,5 vs 234,5) y prácticamente iguala al candidato de 35. En **gas** el set de ganancia positiva (19) **mejora** al candidato completo (576,6 vs 580,1) con menos features. Los dos targets **superan a la persistencia** en val (criterio de promoción del [ADR-040](0040-modelo-produccion.md)). El campeón por target sigue siendo **Random Forest** con los mismos `BEST_PARAMS` (petróleo `n_estimators=200, max_depth=24, max_features=0.5, min_samples_leaf=5`; gas `400/16/0.5/5`).

## Consecuencias

**Positivas:**
- Corte **empírico y auditable** (`imp_mean > 0`), sin umbral de parsimonia arbitrario; sale directo de `ml.selection.select_features`.
- **Mejor o igual RMSE en val** que el set compacto, con la misma familia de modelo e hiperparámetros.
- **Más señal estática para el cold-start** (categóricas del pozo además de las anclas del ADR-043).
- Sigue siendo **recursion-safe**: el `/forecast` no cambia de contrato ni de motor.

**Negativas / trade-offs:**
- Más columnas en el feature store (27/19 vs 14) y one-hot más grande por las categóricas de alta cardinalidad (`empresa`). Costo trivial al volumen, pero real en tamaño de artefacto.
- El corte `imp > 0` conserva algunas categóricas con importancia **dentro de su propio ruido** (`imp ≈ 0,00–0,05 < imp_std`): aportan poco y son candidatas a una poda futura con un corte más estricto (`imp > imp_std`).
- Las métricas reportadas son de **val**; la confirmación en test se hace en el retrain (ADR-040), que compara campeón vs persistencia y vs Production vigente en cada corrida y solo promueve si mejora — la vara no depende de esta tabla.
- Requiere **re-materializar el store** y recomputar el forecast precomputado (ADR-045) en la próxima corrida de retrain para que la API sirva el set nuevo.

---

> Relacionados: **ADR-043** (ranking, metodología y análisis de cold-start que este ADR reutiliza; se revisa solo su *Decisión* de recorte a 14), **ADR-040** (criterio de promoción y campeón por target, independiente del tamaño del set), **ADR-036** (feature store: columnas a re-materializar), **ADR-044/045** (forecast recursivo y precomputado que consumen `selected_features`), **ADR-042** (modelo de gas), **ADR-039** (imputación 0 + flag). Metodología en `ml/selection.py` y notebooks `02`/`03`.
