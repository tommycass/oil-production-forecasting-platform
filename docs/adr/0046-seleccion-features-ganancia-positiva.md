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

Fijar el set del modelo en las **features de ganancia positiva** (`imp_mean > 0` del ranking del ADR-043), **por target**. Las tablas listan **todas** las features que entrena cada modelo, en orden de importancia (permutation importance en val, m³).

**Petróleo (`prod_pet`) — 27 features:**

| # | Feature | Imp. (m³) | Tipo |
|---|---|---:|---|
| 1 | `prod_pet` | 461,68 | autorregresiva (nivel / persistencia) |
| 2 | `prod_pet_roll3` | 113,39 | autorregresiva (nivel reciente) |
| 3 | `prod_pet_ratio1` | 24,48 | autorregresiva (declinación mult.) |
| 4 | `prod_pet_acum12` | 23,92 | autorregresiva (volumen 12m) |
| 5 | `prod_pet_roll6` | 23,33 | autorregresiva (nivel 6m) |
| 6 | `prod_pet_acum6` | 22,46 | autorregresiva (volumen 6m) |
| 7 | `areayacimiento` | 13,85 | estática (reservorio) — ancla cold-start |
| 8 | `profundidad` | 10,24 | estática (física) — ancla cold-start |
| 9 | `prod_pet_cummax` | 4,00 | autorregresiva (pico histórico) |
| 10 | `prod_pet_lag2` | 2,98 | autorregresiva (nivel t-2) |
| 11 | `prod_pet_delta1` | 1,80 | autorregresiva (declinación abs.) |
| 12 | `prod_pet_std3` | 1,32 | autorregresiva (volatilidad) |
| 13 | `prod_pet_frac_peak` | 1,16 | autorregresiva (etapa declinación) |
| 14 | `prod_pet_lag12` | 0,98 | autorregresiva (estacional anual) |
| 15 | `prod_pet_lag3` | 0,88 | autorregresiva (nivel t-3) |
| 16 | `coordenaday` | 0,82 | estática (ubicación) |
| 17 | `coordenadax` | 0,77 | estática (ubicación) |
| 18 | `well_age_months` | 0,68 | estática (edad) — marca el cold-start |
| 19 | `areapermisoconcesion` | 0,64 | categórica (área) |
| 20 | `tipopozo` | 0,59 | categórica |
| 21 | `empresa` | 0,49 | categórica (alta cardinalidad) |
| 22 | `prod_pet_meses_desde_pico` | 0,24 | autorregresiva |
| 23 | `mes` | 0,17 | calendario (estacionalidad) |
| 24 | `tipoextraccion` | 0,04 | categórica |
| 25 | `produjo_mes_pasado` | 0,01 | flag de actividad |
| 26 | `proyecto` | 0,00 | categórica |
| 27 | `cuenca` | 0,00 | categórica |

**Gas (`prod_gas`) — 19 features:**

| # | Feature | Imp. (m³) | Tipo |
|---|---|---:|---|
| 1 | `prod_gas` | 765,26 | autorregresiva (nivel / persistencia) |
| 2 | `prod_gas_roll3` | 254,68 | autorregresiva (nivel reciente) |
| 3 | `prod_gas_roll6` | 59,79 | autorregresiva (nivel 6m) |
| 4 | `prod_gas_acum12` | 55,80 | autorregresiva (volumen 12m) |
| 5 | `prod_gas_acum6` | 54,91 | autorregresiva (volumen 6m) |
| 6 | `prod_gas_ratio1` | 17,22 | autorregresiva (declinación mult.) |
| 7 | `tipoestado` | 13,16 | categórica — única estática con señal en gas |
| 8 | `prod_gas_lag3` | 2,10 | autorregresiva (nivel t-3) |
| 9 | `prod_gas_lag12` | 1,80 | autorregresiva (estacional anual) |
| 10 | `prod_gas_std3` | 1,80 | autorregresiva (volatilidad) |
| 11 | `prod_gas_cummax` | 1,60 | autorregresiva (pico histórico) |
| 12 | `prod_gas_delta3` | 0,98 | autorregresiva (declinación 3m) |
| 13 | `mes` | 0,59 | calendario (estacionalidad) |
| 14 | `prod_gas_lag2` | 0,34 | autorregresiva (nivel t-2) |
| 15 | `clasificacion` | 0,01 | categórica |
| 16 | `sub_tipo_recurso` | 0,00 | categórica |
| 17 | `provincia` | 0,00 | categórica |
| 18 | `formprod` | 0,00 | categórica |
| 19 | `tipoextraccion` | 0,00 | categórica |

Los dos sets **difieren en estructura** (no es solo cambiar el prefijo del target): en gas, las anclas geográficas/físicas del cold-start de petróleo (`areayacimiento`, `profundidad`, `coordenadax/y`, `well_age_months`) tienen importancia **negativa** y **no entran** — el gas queda con `tipoestado` como única estática con señal. Es una asimetría real que el corte empírico expone: el cold-start de gas se apoya casi solo en categóricas (ver *Consecuencias*).

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

**Confirmación en test** (entrenamiento final `--final`: dev = train+val, evaluado una vez en test, con el set de ganancia positiva):

| Target | Features | test RMSE | test R² | persistencia (test) |
|---|---:|---:|---:|---|
| Petróleo (`prod_pet`) | 27 | **157,5** | **0,869** | 166,2 / 0,854 |
| Gas (`prod_gas`) | 19 | **409,2** | **0,850** | 457,8 / 0,813 |

Los dos superan a la persistencia también en test. Frente al set compacto de 14 (ADR-043 §4: test RMSE 164,7 / R² 0,856 en petróleo), el set de ganancia positiva **mejora** el test de petróleo (157,5 / 0,869) y deja el de gas prácticamente igual.

## Consecuencias

**Positivas:**
- Corte **empírico y auditable** (`imp_mean > 0`), sin umbral de parsimonia arbitrario; sale directo de `ml.selection.select_features`.
- **Mejor o igual RMSE en val** que el set compacto, con la misma familia de modelo e hiperparámetros.
- **Más señal estática para el cold-start** (categóricas del pozo además de las anclas del ADR-043).
- Sigue siendo **recursion-safe**: el `/forecast` no cambia de contrato ni de motor.

**Negativas / trade-offs:**
- Más columnas en el feature store (27/19 vs 14) y one-hot más grande por las categóricas de alta cardinalidad (`empresa`). Costo trivial al volumen, pero real en tamaño de artefacto.
- El corte `imp > 0` conserva algunas categóricas con importancia **dentro de su propio ruido** (`imp ≈ 0,00–0,05 < imp_std`): aportan poco y son candidatas a una poda futura con un corte más estricto (`imp > imp_std`).
- La selección se hizo sobre **val** (test intacto); la **confirmación en test** (arriba) valida el set una vez, pero la vara de promoción no depende de esta tabla: el retrain compara campeón vs persistencia y vs Production vigente en cada corrida y solo promueve si mejora (ADR-040).
- Requiere **re-materializar el store** y recomputar el forecast precomputado (ADR-045) en la próxima corrida de retrain para que la API sirva el set nuevo.

---

> Relacionados: **ADR-043** (ranking, metodología y análisis de cold-start que este ADR reutiliza; se revisa solo su *Decisión* de recorte a 14), **ADR-040** (criterio de promoción y campeón por target, independiente del tamaño del set), **ADR-036** (feature store: columnas a re-materializar), **ADR-044/045** (forecast recursivo y precomputado que consumen `selected_features`), **ADR-042** (modelo de gas), **ADR-039** (imputación 0 + flag). Metodología en `ml/selection.py` y notebooks `02`/`03`.
