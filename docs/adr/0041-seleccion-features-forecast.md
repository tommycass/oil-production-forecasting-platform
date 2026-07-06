# Título: ADR-041: Selección de features del forecast (permutation importance, ganancia positiva)

**Estado:** Aceptada

## Contexto

El forecast es **recursivo** (predecir t+1, t+2, … realimentando la propia predicción), así que el modelo se construye sobre un set **recursion-safe**: features recalculables en un mes futuro a partir de la trayectoria del propio target. Ese set junta la ingeniería del ADR-033 con las autorregresivas del target (`{target}_lag2/lag3/roll6/acum12/delta1/delta3/ratio1/std3/cummax/frac_peak/meses_desde_pico` + `well_age_months`) y **excluye** las que no se recalculan hacia el futuro (`prod_vecinos_mean` cross-well, `water_cut`, el target cruzado, `prod_agua`, `tef`). Queda un **candidato de 35 features recursion-safe**.

Con 35 candidatas hay que **decidir el set del modelo**, con tres criterios en tensión: (1) **poder predictivo** — qué features bajan el error; (2) **robustez a cold-start** — pozos sin historial, donde las autorregresivas quedan `NaN`; (3) **mantenibilidad del store** — cada feature es una columna a materializar con paridad training-serving (ADR-035).

**Metodología (leakage-safe).** Se rankea por **permutation importance sobre `val`** aplicada al campeón (Random Forest tuneado con CV temporal, ADR-034), en `notebooks/02_feature_selection_pet.ipynb` (petróleo) y `03_feature_selection_gas.ipynb` (gas), con la lógica en `ml/selection.py`. Se mide en val (no train) para no premiar lo memorizado; **`test` queda intacto**. La granularidad es la **feature cruda** (se permuta `empresa` entera, no una dummy). La importancia queda en **m³** = cuánto sube el RMSE de val al **romper** esa feature: `> 0` = útil, `≈ 0` o `< 0` = ruido.

## Ranking de features (petróleo, `prod_pet`)

| # | Feature | Imp. (m³) | Tipo | Decisión |
|---|---|---:|---|---|
| 1 | `prod_pet` | 461,68 | autorregresiva (nivel / persistencia) | **Mantener** |
| 2 | `prod_pet_roll3` | 113,39 | autorregresiva (nivel reciente) | **Mantener** |
| 3 | `prod_pet_ratio1` | 24,48 | autorregresiva (declinación mult.) | **Mantener** |
| 4 | `prod_pet_acum12` | 23,92 | autorregresiva (volumen 12m) | **Mantener** |
| 5 | `prod_pet_roll6` | 23,33 | autorregresiva (nivel 6m) | **Mantener** |
| 6 | `prod_pet_acum6` | 22,46 | autorregresiva (volumen 6m) | **Mantener** |
| 7 | `areayacimiento` | 13,85 | estática (reservorio) | **Mantener — ancla cold-start** |
| 8 | `profundidad` | 10,24 | estática (física) | **Mantener — ancla cold-start** |
| 9 | `prod_pet_cummax` | 4,00 | autorregresiva (pico histórico) | **Mantener** |
| 10 | `prod_pet_lag2` | 2,98 | autorregresiva (nivel t-2) | **Mantener** |
| 11 | `prod_pet_delta1` | 1,80 | autorregresiva (declinación abs.) | **Mantener** |
| 12 | `prod_pet_std3` | 1,32 | autorregresiva (volatilidad) | **Mantener** |
| 13 | `prod_pet_frac_peak` | 1,16 | autorregresiva (etapa declinación) | **Mantener** |
| 14 | `prod_pet_lag12` | 0,98 | autorregresiva (estacional anual) | **Mantener** |
| 15 | `prod_pet_lag3` | 0,88 | autorregresiva (nivel t-3) | **Mantener** |
| 16 | `coordenaday` | 0,82 | estática (ubicación) | **Mantener — ancla cold-start** |
| 17 | `coordenadax` | 0,77 | estática (ubicación) | **Mantener — ancla cold-start** |
| 18 | `well_age_months` | 0,68 | estática (edad) | **Mantener — marca el cold-start** |
| 19 | `areapermisoconcesion` | 0,64 | categórica (área) | **Mantener** |
| 20 | `tipopozo` | 0,59 | categórica | **Mantener** |
| 21 | `empresa` | 0,49 | categórica (alta card.) | **Mantener** |
| 22 | `prod_pet_meses_desde_pico` | 0,24 | autorregresiva | **Mantener** |
| 23 | `mes` | 0,17 | calendario (estacionalidad) | **Mantener** |
| 24 | `tipoextraccion` | 0,04 | categórica | **Mantener** |
| 25 | `produjo_mes_pasado` | 0,01 | flag actividad | **Mantener** |
| 26 | `proyecto` | 0,00 | categórica | **Mantener** |
| 27 | `cuenca` | 0,00 | categórica | **Mantener** |
| 28 | `provincia` | -0,00 | categórica | Descartar |
| 29 | `subclasificacion` | -0,00 | categórica | Descartar |
| 30 | `clasificacion` | -0,00 | categórica | Descartar |
| 31 | `tipoestado` | -0,01 | categórica | Descartar |
| 32 | `sub_tipo_recurso` | -0,02 | categórica | Descartar |
| 33 | `formacion` | -0,05 | categórica | Descartar |
| 34 | `formprod` | -0,06 | categórica | Descartar |
| 35 | `prod_pet_delta3` | -0,20 | autorregresiva | **Descartar — ruido (imp. negativa)** |

El corte cae **naturalmente en la ganancia positiva**: las **filas 1–27 tienen `imp > 0`** (romperlas empeora el RMSE de val → aportan) y las **28–35 son `≤ 0`** (ruido, `delta3` hasta negativa). La señal es abrumadoramente **autorregresiva** (`prod_pet` sola pesa ~4× la siguiente); entre las estáticas destacan `areayacimiento` y `profundidad`; las categóricas de baja cardinalidad quedan en el límite (imp ≈ 0) y las de imp ≤ 0 se descartan.

## El problema del cold-start

Para un pozo **sin historial** (primeros meses de su serie), **todas** las autorregresivas —incluida la dominante `prod_pet`— son `NaN` y se imputan a **0 + flag** (ADR-038): el motor de casi toda la señal **no existe** en esas filas y la predicción se apoya **solo** en las estáticas + flags. Esto tiene una consecuencia metodológica: la permutation importance sobre toda la población **subestima** el valor de las estáticas para el cold-start (se promedia sobre val, donde casi todas las filas tienen historia). Por eso el ranking se lee con ese matiz — las anclas estáticas de petróleo (`areayacimiento`, `profundidad`, `coordenadax/y`, `well_age_months`) valen más de lo que su importancia global sugiere. Todas caen del lado `imp > 0`, así que el criterio de ganancia positiva las conserva sin necesidad de una regla aparte.

> **Alcance del término.** "Cold-start" = pozo **dentro del universo** de entrenamiento pero al inicio de su serie. Un pozo **totalmente nuevo** (que nunca produjo el target en train) no tiene fila en el store → hoy no es predecible (limitación de serving, ADR-031), no de selección.

## Análisis de Alternativas

### 1. Criterio de corte sobre el ranking

- **Top-k puro por importancia (descartado):** maximizaría el RMSE de la población general pero dejaría al cold-start sin señal (las top son todas autorregresivas → `NaN` en pozos nuevos).
- **Ganancia positiva `imp_mean > 0` (elegido):** se conserva **toda feature que reduce el RMSE de val** — corte **empírico y no subjetivo** (lo devuelve `ml.selection.select_features`). Da **27 features en petróleo, 19 en gas**. Cubre naturalmente el cold-start: las anclas estáticas de petróleo tienen imp > 0 y entran solas.
- **Corte compacto por parsimonia (descartado):** quedarse con un puñado (~10–14) podando las de aporte chico. Da un modelo más liviano pero **cede RMSE** (en petróleo ~3% en val) por un umbral arbitrario, y descarta features que el ranking marca como útiles.
- **Estricto `imp_mean > imp_std` (descartado por ahora):** dejar solo las que superan su propio ruido. Es defendible (algunas categóricas conservadas tienen imp ≈ 0,00–0,05, dentro de su `imp_std`), pero reintroduce un umbral de decisión; se deja anotado como recorte alternativo re-evaluable.

### 2. Categóricas de importancia ≤ 0

- **Mantener todas (descartado):** inflaría el one-hot (cientos de dummies) sin señal, más columnas a mantener y más ruido para el lineal.
- **Descartar las de imp ≤ 0 (elegido):** en petróleo `provincia`, `subclasificacion`, `clasificacion`, `tipoestado`, `sub_tipo_recurso`, `formacion`, `formprod`. Buena parte de la señal geológica que podrían aportar ya la captura `areayacimiento` (correlacionada), que sí entra.

### 3. Hiperparámetros del modelo

- **Re-tunear sobre el set final (descartado):** el tuning (notebooks 02/03) se corrió sobre el candidato de **35**, y las 27/19 son un **subconjunto** de ese espacio; el campeón con esos `BEST_PARAMS` ya se validó sobre el recorte en val **y** test (abajo), superando la persistencia. Re-tunear ahora se apartaría de esta misma metodología. La única sensibilidad real al tamaño del set es `max_features=0.5` (fracción), cuyo efecto queda absorbido por esa validación empírica. **Se mantienen los `BEST_PARAMS` vigentes.**

## Decisión

Fijar el set del modelo en las **features de ganancia positiva** (`imp_mean > 0`), **por target**. Es la Decisión de las tres puntas: entrenamiento, feature store y forecast.

**Petróleo (`prod_pet`) — 27 features:** las **filas 1–27** del ranking de arriba (núcleo autorregresivo + anclas estáticas + categóricas de baja cardinalidad con imp > 0).

**Gas (`prod_gas`) — 19 features** (ranking análogo del notebook 03):

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

**Asimetría petróleo/gas.** Los sets **difieren en estructura** (no es solo cambiar el prefijo): en gas, las anclas geográficas/físicas del cold-start de petróleo (`areayacimiento`, `profundidad`, `coordenadax/y`, `well_age_months`) tienen importancia **negativa** y **no entran** — el gas queda con `tipoestado` como única estática con señal, y su cold-start se apoya casi solo en categóricas (ver *Consecuencias*).

**Descartadas** (imp ≤ 0): en petróleo `provincia`, `subclasificacion`, `clasificacion`, `tipoestado`, `sub_tipo_recurso`, `formacion`, `formprod`, `prod_pet_delta3` (negativa); en gas sus análogas y `prod_gas_delta1`/`frac_peak`/`meses_desde_pico` + las estáticas geográficas. Fuera del candidato por recursion-safety: `prod_vecinos_mean`, `water_cut`, target cruzado, `prod_agua`, `tef`.

**Implementación (fuente única de verdad):** el set vive en `ml.features.selected_features(target)`, que devuelve listas **distintas por target**. Lo consumen sin poder divergir el entrenamiento (`ml/train.py` con `build_feature_matrix(target, selected=True)`), el feature store (`feature_store_build.py`, ADR-035) y el serving (`/forecast` recursivo, ADR-042/043). Se conserva la imputación **0 + flag** de las autorregresivas (ADR-038), que hace el cold-start *inspeccionable*.

### Métricas

**Val** (notebooks 02/03: los 3 modelos tuneados sobre el candidato de 35, y el campeón Random Forest reajustado sobre el set de ganancia positiva):

| Modelo / set | Feats | val RMSE | val R² |
|---|---:|---:|---:|
| **RF — ganancia positiva (petróleo)** | **27** | **227,5** | **0,902** |
| RF — candidato (petróleo) | 35 | 227,3 | 0,903 |
| XGBoost — candidato (petróleo) | 35 | 236,9 | 0,894 |
| Ridge — candidato (petróleo) | 35 | 239,0 | 0,892 |
| Persistencia (petróleo) | — | 250,6 | 0,881 |
| **RF — ganancia positiva (gas)** | **19** | **576,6** | **0,861** |
| RF — candidato (gas) | 35 | 580,1 | 0,859 |
| Persistencia (gas) | — | 635,5 | 0,831 |

**Test** (entrenamiento final `--final`: dev = train+val, evaluado una vez en test, con el set de ganancia positiva):

| Target | Feats | test RMSE | test R² | persistencia (test) |
|---|---:|---:|---:|---|
| Petróleo (`prod_pet`) | 27 | **157,5** | **0,869** | 166,2 / 0,854 |
| Gas (`prod_gas`) | 19 | **409,2** | **0,850** | 457,8 / 0,813 |

Los dos targets **superan a la persistencia** en val y en test (criterio de promoción del ADR-039). El campeón es **Random Forest** con `BEST_PARAMS` petróleo `n_estimators=200, max_depth=24, max_features=0.5, min_samples_leaf=5` y gas `400/16/0.5/5`.

## Consecuencias

**Positivas:**
- Corte **empírico y auditable** (`imp_mean > 0`), sin umbral de parsimonia arbitrario; sale directo del ranking de los notebooks (reproducible, leakage-safe).
- **Robustez a cold-start** en petróleo: las anclas estáticas caen del lado positivo y se conservan solas.
- El campeón supera la persistencia en **val y test** con estos features e hiperparámetros.
- Baja `delta3` (ruido) y las categóricas de imp ≤ 0, detectadas por el propio ranking.

**Negativas / trade-offs:**
- Más columnas en el store (27/19) y one-hot más grande por las categóricas (`empresa`, alta cardinalidad). Costo trivial al volumen (~6 k filas/tabla, ADR-035) pero real en tamaño de artefacto.
- El corte conserva algunas categóricas con importancia **dentro de su propio ruido** (`imp ≈ 0,00–0,05 < imp_std`): candidatas a una poda futura con `imp > imp_std`.
- **Cold-start de gas más débil:** sin anclas geográficas/físicas (imp ≤ 0), depende casi solo de `tipoestado` y categóricas. Mitigable con features de cold-start dedicadas en un rediseño futuro.
- La importancia se midió sobre la **población general**; una calibración fina de las estáticas debería medirse sobre la **subpoblación cold-start** (pendiente).
- El **store** (ADR-035) debe materializar exactamente estas columnas con paridad training-serving, y el forecast recursivo necesita la **serie histórica** del pozo para sembrar la recursión.

---

> Relacionados: **ADR-033** (feature engineering base que este ADR poda), **ADR-038** (imputación 0 + flag, clave para el cold-start), **ADR-031** (universo train-only y anti-leakage), **ADR-035** (feature store: columnas a materializar), **ADR-034** (algoritmo y CV temporal, sobre cuyo campeón se midió la importancia), **ADR-039** (campeón y criterio de promoción), **ADR-042/043** (forecast recursivo y precomputado que consumen `selected_features`) y **ADR-039** (modelo de gas: misma selección con `prod_gas_*`). Metodología en `ml/selection.py` y notebooks `02`/`03`.
