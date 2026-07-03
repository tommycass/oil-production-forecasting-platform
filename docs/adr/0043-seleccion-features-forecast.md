# Título: ADR-043: Selección de features del forecast (ranking por permutation importance y robustez a cold-start)

**Estado:** Aceptada

## Contexto

El objetivo del forecast es **recursivo** (predecir t+1, t+2, … realimentando la propia predicción), así que el modelo se construye desde el inicio sobre un set **recursion-safe**: features que se pueden recalcular en un mes futuro a partir de la trayectoria del propio target. Ese set junta las features de ingeniería del ADR-033 con las autorregresivas del propio target (`prod_pet_lag2/lag3/roll6/acum12/delta3/ratio1/std3/cummax/frac_peak/meses_desde_pico` + `well_age_months`), y **excluye** las que no se pueden recalcular hacia el futuro (`prod_vecinos_mean` —cross-well—, `water_cut`, `prod_gas`, `prod_agua`, `tef`). Queda un **set candidato de 35 features recursion-safe**.

Con 35 candidatas hace falta **decidir el set final**, con tres criterios en tensión:

1. **Poder predictivo** — qué features realmente bajan el error.
2. **Robustez a cold-start** — pozos **sin historial** (primeros meses de su serie): todas las features autorregresivas les quedan `NaN` → el modelo debe apoyarse en otra cosa.
3. **Mantenibilidad del feature store** — cada feature es una columna a materializar y mantener con paridad training-serving (ADR-036).

**Metodología (leakage-safe).** El ranking se calculó con **permutation importance sobre `val`** aplicada al modelo campeón (Random Forest tuneado con CV temporal), en `notebooks/05_feature_selection_pet.ipynb`, con la lógica en `ml/selection.py`. Se mide en val (no en train) para no premiar lo que el modelo memorizó; **`test` quedó intacto**. La granularidad es la **feature cruda** (se permuta `empresa` entera, no una dummy suelta). La importancia queda en **m³** = cuánto sube el RMSE de val al romper esa feature.

## Ranking de features (petróleo, `prod_pet`)

| # | Feature | Imp. (m³) | Tipo | Decisión |
|---|---|---:|---|---|
| 1 | `prod_pet` | 461.68 | autorregresiva (persistencia / lag1) | **Mantener** |
| 2 | `prod_pet_roll3` | 113.39 | autorregresiva (nivel reciente) | **Mantener** |
| 3 | `prod_pet_ratio1` | 24.48 | autorregresiva (declinación mult.) | **Mantener** |
| 4 | `prod_pet_acum12` | 23.92 | autorregresiva (volumen 12m) | **Mantener** |
| 5 | `prod_pet_roll6` | 23.33 | autorregresiva (nivel 6m) | **Mantener** |
| 6 | `prod_pet_acum6` | 22.46 | autorregresiva (volumen 6m) | **Mantener** |
| 7 | `areayacimiento` | 13.85 | estática (reservorio) | **Mantener — ancla cold-start** |
| 8 | `profundidad` | 10.24 | estática (física) | **Mantener — ancla cold-start** |
| 9 | `prod_pet_cummax` | 4.00 | autorregresiva (pico histórico) | **Mantener** |
| 10 | `prod_pet_lag2` | 2.98 | autorregresiva (nivel t-2) | **Mantener** |
| 11 | `prod_pet_delta1` | 1.80 | autorregresiva (declinación abs.) | Mantener (núcleo, aporte chico) |
| 12 | `prod_pet_std3` | 1.32 | autorregresiva (volatilidad) | Borderline |
| 13 | `prod_pet_frac_peak` | 1.16 | autorregresiva (etapa declinación) | Borderline |
| 14 | `prod_pet_lag12` | 0.98 | autorregresiva (estacional anual) | Borderline |
| 15 | `prod_pet_lag3` | 0.88 | autorregresiva (nivel t-3) | Borderline |
| 16 | `coordenaday` | 0.82 | estática (ubicación) | **Mantener — ancla cold-start** |
| 17 | `coordenadax` | 0.77 | estática (ubicación) | **Mantener — ancla cold-start** |
| 18 | `well_age_months` | 0.68 | estática/edad | **Mantener — marca el cold-start** |
| 19 | `areapermisoconcesion` | 0.64 | estática (área) | Borderline (correl. con areayacimiento) |
| 20 | `tipopozo` | 0.59 | categórica | Borderline |
| 21 | `empresa` | 0.49 | categórica (alta card.) | Borderline |
| 22 | `prod_pet_meses_desde_pico` | 0.24 | autorregresiva | Borderline |
| 23 | `mes` | 0.17 | calendario (estacionalidad) | Borderline |
| 24 | `tipoextraccion` | 0.04 | categórica | Descartar |
| 25 | `produjo_mes_pasado` | 0.01 | flag actividad | Descartar |
| 26 | `proyecto` | 0.00 | categórica | Descartar |
| 27 | `cuenca` | 0.00 | categórica | Descartar |
| 28 | `provincia` | -0.00 | categórica | Descartar |
| 29 | `subclasificacion` | -0.00 | categórica | Descartar |
| 30 | `clasificacion` | -0.00 | categórica | Descartar |
| 31 | `tipoestado` | -0.01 | categórica | Descartar |
| 32 | `sub_tipo_recurso` | -0.02 | categórica | Descartar |
| 33 | `formacion` | -0.05 | categórica | Descartar |
| 34 | `formprod` | -0.06 | categórica | Descartar |
| 35 | `prod_pet_delta3` | -0.20 | autorregresiva | **Descartar — ruido (imp. negativa)** |

Lectura: la señal es **abrumadoramente autorregresiva** (`prod_pet` sola pesa ~4× la siguiente); un puñado de features de nivel/tendencia/volumen (`roll3`, `ratio1`, `acum12`, `roll6`, `acum6`) concentra casi todo; entre las estáticas solo `areayacimiento` y `profundidad` aportan de forma visible; la mayoría de las categóricas de baja cardinalidad son **peso muerto** (≈ 0 o negativas), y `delta3` es directamente ruido.

## El problema del cold-start (por qué no basta con "tomar las top")

Para un pozo **sin historial** (primeros meses de su serie), **todas** las features autorregresivas —incluida la dominante `prod_pet` (imp. 461)— son `NaN` y se imputan a **0 + flag** (ADR-039). Es decir, el motor predictivo de casi toda la señal **no existe** en esas filas: la predicción queda apoyada **solo** en las estáticas + los flags de faltante.

Esto tiene una consecuencia metodológica fuerte: **la permutation importance sobre toda la población subestima el valor de las estáticas para el cold-start**. La importancia se promedia sobre todo `val`, donde la **enorme mayoría** de las filas tienen historia y se apoyan en lo autorregresivo; permutar `areayacimiento` mueve poco el RMSE global (13.85) porque casi ningún pozo la *necesita*. Pero para la **subpoblación cold-start**, `areayacimiento` + `profundidad` + ubicación son **la única señal disponible**.

Por eso, seleccionar "top-k por importancia" a secas sería un error: dejaría al modelo **ciego** frente a pozos nuevos. La regla correcta es **importancia + retención explícita de anclas estáticas**.

Rol de cada ancla en cold-start:
- **`areayacimiento`** — un pozo nuevo en un reservorio conocido se puede aproximar por la producción típica de ese yacimiento. Es la estática más informativa.
- **`profundidad`** — proxy físico de productividad, disponible desde el día 0.
- **`coordenadax/y`** — contexto espacial (qué hay alrededor), aunque sin la agregación de vecinos su aporte es débil.
- **`well_age_months`** — para un pozo nuevo vale ≈ 0, lo que **marca explícitamente el cold-start** y deja que el modelo aplique otro régimen.

**Alcance del término.** "Cold-start" acá = pozo **dentro del universo** de entrenamiento pero al inicio de su serie. Un pozo **totalmente nuevo** (que nunca produjo el target en train) no tiene fila en el feature store → hoy no es predecible (404, ADR-031/serving); esa brecha es una limitación de serving aparte, no de selección de features.

## Análisis de Alternativas

### 1. Criterio de selección

- **Top-k puro por importancia (descartado):** maximiza el RMSE en la población general pero deja al cold-start sin señal (las top son todas autorregresivas → `NaN` en pozos nuevos).
- **Importancia + anclas estáticas (elegido):** se toma el núcleo autorregresivo (lo que gana en pozos con historia) **más** las estáticas informativas (lo que sostiene al cold-start), aunque estas últimas tengan importancia global modesta.
- **Importancia medida sobre la subpoblación cold-start (ideal, pendiente):** repetir el ranking filtrando a los primeros N meses de cada pozo daría la importancia *real* de las estáticas ahí. Queda como mejora (permite calibrar cuántas estáticas conservar con evidencia, no por criterio).

### 2. Categóricas de importancia ≈ 0

- **Mantener todas (descartado):** infla el one-hot (cientos de dummies) sin señal; más columnas a mantener con paridad y más ruido para el lineal.
- **Descartar las de baja cardinalidad con imp. ≈ 0 / negativa (elegido):** `formacion`, `formprod`, `cuenca`, `provincia`, `clasificacion`, `subclasificacion`, `tipoestado`, `sub_tipo_recurso`, `tipoextraccion`, `proyecto`. Nota: buena parte de la señal geológica que *podrían* aportar ya está capturada por `areayacimiento` (correlacionada), que sí se conserva.

### 3. Features autorregresivas débiles y `delta3`

- **`delta3` (descartada):** importancia **negativa** (−0.20) → permutarla *mejora* el modelo; es ruido. Es una de las features nuevas y el ranking la descarta empíricamente.
- **Núcleo autorregresivo (mantenido):** `prod_pet`, `roll3`, `ratio1`, `acum12`, `roll6`, `acum6`, `cummax`, `lag2`, `delta1`. Cubren nivel, tendencia, declinación (abs. y mult.), volumen y pico.
- **Borderline (`std3`, `frac_peak`, `lag12`, `lag3`, `meses_desde_pico`, `mes`):** aporte < 1.5 m³. Se pueden conservar por costo bajo o podar por parsimonia; se recomienda **re-tunear + medir con y sin ellas** antes de fijar.

### 4. Validación empírica del recorte (val y test)

Se entrenaron los 3 modelos usando **solo las 10 features top** (hasta `prod_pet_lag2`), en train, evaluando en val **y** test:

| modelo | val_rmse | val_r2 | test_rmse | test_r2 |
|---|---:|---:|---:|---:|
| random_forest | 234.5 | 0.896 | 164.7 | 0.856 |
| xgboost | 239.0 | 0.892 | 167.5 | 0.851 |
| ridge | 242.4 | 0.889 | 177.2 | 0.834 |
| persistencia | 250.6 | 0.881 | 166.2 | 0.854 |

- En **val**, el set chico se sostiene: RF 234.5 vs 227.8 del set completo de 35 (~3% de costo por tirar 25 features).
- En **test**, la ventaja sobre la persistencia **casi desaparece** (RF le gana por ~1%; xgboost y ridge quedan por debajo). El RMSE absoluto de test es menor porque el período tiene producciones más chicas — la comparación válida entre splits es el **R²** (0.896 → 0.856). Es un resultado honesto: el modelo generaliza, pero su *edge* se erosiona fuera de val.

## Decisión

Adoptar, para el modelo de **petróleo**, un set final en **dos capas**, con el mismo criterio aplicable simétricamente a **gas** (`prod_gas_*` en vez de `prod_pet_*`, y `prod_pet` como serie excluida):

**Capa 1 — Motor autorregresivo (pozos con historial):**
`prod_pet`, `prod_pet_roll3`, `prod_pet_ratio1`, `prod_pet_acum12`, `prod_pet_roll6`, `prod_pet_acum6`, `prod_pet_cummax`, `prod_pet_lag2`, `prod_pet_delta1`.

**Capa 2 — Anclas estáticas (sostienen el cold-start):**
`areayacimiento`, `profundidad`, `coordenadax`, `coordenaday`, `well_age_months`.

**Descartadas:**
- `prod_pet_delta3` (importancia negativa).
- Categóricas de baja cardinalidad con imp. ≈ 0: `formacion`, `formprod`, `cuenca`, `provincia`, `clasificacion`, `subclasificacion`, `tipoestado`, `sub_tipo_recurso`, `tipoextraccion`, `proyecto`, `produjo_mes_pasado`.
- (Ya fuera del candidato por recursion-safety: `prod_vecinos_mean`, `water_cut`, `prod_gas`, `prod_agua`, `tef`.)

**Borderline (a fijar con re-tuning + medición dedicada):** `std3`, `frac_peak`, `lag12`, `lag3`, `meses_desde_pico`, `mes`, `areapermisoconcesion`, `tipopozo`, `empresa`.

Se conserva el esquema de imputación **0 + flag** para las autorregresivas (ADR-039): es lo que hace que el cold-start sea *inspeccionable* (el flag distingue "sin historia" de "produjo 0").

## Consecuencias

**Positivas:**
- Set **compacto** (~14 features fijas) que en val casi iguala al de 35 (RF 234.5 vs 227.8) → menos columnas a materializar y mantener (ADR-036), modelo más simple y rápido.
- **Robustez a cold-start explícita:** al conservar las anclas estáticas, un pozo nuevo tiene señal (reservorio, profundidad, ubicación) aunque sus features autorregresivas estén en 0 + flag.
- Decisión **empírica y auditable:** el ranking sale del notebook 05, reproducible y leakage-safe.
- Baja `delta3` (ruido) detectada por el propio ranking.

**Negativas / trade-offs:**
- El cold-start **sigue siendo el punto débil**: sin la señal autorregresiva dominante, la predicción de pozos nuevos es intrínsecamente más pobre. Este ADR lo *mitiga* (anclas), no lo resuelve.
- La importancia se midió sobre la **población general**; la calibración fina de cuántas estáticas conservar debería hacerse sobre la **subpoblación cold-start** (pendiente).
- La erosión del *edge* sobre la persistencia en **test** sugiere posible cambio de distribución entre períodos; conviene monitorearlo y no sobre-vender la mejora del modelo.
- El **contrato del feature store** (ADR-036) debe materializar exactamente estas columnas recursion-safe con paridad training-serving, y el forecast recursivo necesita además la **serie histórica** del pozo para sembrar la recursión (no solo la fila del mes base).

---

> Relacionados: **ADR-033** (feature engineering base que este ADR extiende y poda), **ADR-039** (imputación 0 + flag, clave para el cold-start), **ADR-031** (universo train-only y anti-leakage; define qué pozo *tiene* fila), **ADR-036** (feature store: contrato de columnas a re-materializar), **ADR-034** (algoritmo y CV temporal, sobre cuyo campeón se midió la importancia) y **ADR-042** (modelo de gas: misma selección con features `prod_gas_*`). Metodología en `ml/selection.py` y `notebooks/05_feature_selection_pet.ipynb`.
