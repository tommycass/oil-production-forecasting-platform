# Auditoría de consistencia — ADRs Fase 3

**Alcance:** ADR-028 a ADR-043 (16 ADRs) cruzados entre sí **y** contra el código.
**Fecha:** 2026-07-08. **Método:** 3 barridos (ADR↔ADR, ADR↔código modelado, ADR↔código infra) + verificación manual de los hallazgos críticos contra el código que corre hoy.

> Regla para resolver: **ante ADR vs código, el código es la verdad** salvo que el código tenga un bug (ahí lo marco como "decisión del equipo"). Ante ADR vs ADR, gana el más reciente/Aceptado, y el viejo debe marcarse.

---

## Resumen ejecutivo — los 6 que importan

| # | Qué | Gravedad | Dónde |
|---|---|---|---|
| 1 | **`RETRAIN_ASOF` no recorta ningún dato.** El "reproceso como si fuera el día X" no restringe el dataset: solo cambia un `log_param`. | 🔴 Crítico | ADR-040 vs código |
| 2 | **El feature store lee de Bronze, no de Gold.** ADR-033 titula una tabla "Columna(s) Gold"; el código hace `select … from bronze.produccion`. | 🔴 Crítico | ADR-033 vs código |
| 3 | **ADR-039 se autocontradice:** dice que el modelo de gas usa `prod_pet` como feature, y dos párrafos antes dice que usa el set del ADR-041 (que lo excluye). El código: `prod_pet` NO está en el modelo de gas. | 🔴 Crítico | ADR-039 interno + vs código |
| 4 | **ADR-034 dice que el campeón es XGBoost.** El campeón es Random Forest (código + ADR-039). ADR-034 no está marcado como revisado. | 🟠 Alto | ADR-034 vs ADR-039 vs código |
| 5 | **ADR-037 documenta un solo modelo;** el sistema sirve dos (`prod_pet` y `prod_gas`). | 🟠 Alto | ADR-037 vs ADR-039 vs código |
| 6 | **4 citas "textuales" fabricadas:** ADRs que ponen entre comillas frases que el ADR citado no contiene. | 🟠 Alto | ADR-034, 033, 040, 036 |

**Meta-hallazgo:** ningún ADR contiene la palabra "Superseded". Hay ≥3 revisiones de facto (034→039, 033→041, 042 parcial) sin marcar, y **6 ADRs "Aceptada" (035, 039–043) dependen de ADRs que siguen en "Propuesta"** (028, 031, 033, 034, 038). La cátedra lo va a ver.

---

## A. ADR contradice al CÓDIGO

Verificado contra el código que corre hoy. Ordenado por gravedad.

### A1 — 🔴 `RETRAIN_ASOF` no recorta datos (ADR-040)

- **ADR-040:52** afirma: *"`ml.config.retrain_asof()` lee la env var y `build_basic_dataset` recorta `periodo <= asof` antes de definir universo y features"*.
- **Realidad del código:**
  - El entrenamiento lee del store: `ml/baseline.py:70` y `ml/modeling.py:74` usan `build_dataset_from_store()`, **no** `build_basic_dataset()`.
  - `build_dataset_from_store()` (`ml/dataset.py:51`) **no aplica asof** — su docstring dice "el corte se aplica al materializar el store".
  - Pero `feature_store_build.py` (que materializa) **no importa ni llama a `retrain_asof`** (0 referencias), y el asset `features_refrescadas` (`retrain.py`) **no setea `RETRAIN_ASOF`** al materializar.
- **Efecto real:** `RETRAIN_ASOF` solo cambia el `log_param("retrain_asof")` y el hash `data_version` en `ml/registry.py`. **No recorta un solo registro.** La promesa anti-leakage del backfill no se cumple.
- **Decisión del equipo** (no la tomo yo): o (a) **arreglar el código** para que el asof recorte de verdad — lo natural es aplicarlo en `feature_store_build.py` al materializar y que `features_refrescadas` propague `RETRAIN_ASOF`; o (b) **corregir el ADR-040** para admitir que hoy el reproceso por fecha no restringe datos. La opción (a) es la que respeta la intención documentada.

> Corrijo algo que dije antes en esta conversación: afirmé que `RETRAIN_ASOF` recortaba el dataset. Era cierto del camino viejo (`build_basic_dataset`, desde CSV); el entrenamiento migró a leer del store (commit `981627a`) y el recorte quedó huérfano.

### A2 — 🔴 Feature store: Bronze vs Gold (ADR-033)

- **ADR-033:19, :21, :69** dice que las features salen de la **capa Gold** (`gold.fact_produccion_mensual`, `dim_pozo`), y titula la tabla de features *"Columna(s) Gold"*.
- **Código:** `feature_store_build.py:69` → `select {cols} from bronze.produccion`. Lee **Bronze** (crudo, pre-DQ).
- **Efecto colateral:** ADR-038:68 delega la detección de outliers al "data quality de Fase 2 (ADR-016)". Si el store se materializa desde Bronze, **ese DQ no está en el camino**. Es un segundo hilo a reconciliar.
- **Fix:** ADR-033 debe decir Bronze (o el equipo decide mover la materialización a Gold — decisión de arquitectura, no cosmética).

### A3 — 🔴 `prod_pet` en el modelo de gas (ADR-039 interno + código)

- **ADR-039:72** afirma: *"Mantener `prod_pet(t)` como feature del modelo de gas es válido"*.
- **ADR-039:9** (mismo ADR) dice que el modelo usa *"el set de features de la selección (ADR-041), recursion-safe"*.
- **ADR-041:7, :111** excluye explícitamente el "target cruzado" del set.
- **Código:** `selected_features("prod_gas")` → **19 features, `prod_pet` NO está**. Verificado ejecutando.
- **Fix:** eliminar la afirmación de ADR-039:72 (y la mención a `water_cut` en el gas, :93, que tampoco está en el set). Es una autocontradicción del ADR más importante de la fase.

### A4 — 🟠 ADR-037 documenta un solo modelo

- **ADR-037:55-59** lista un `MLFLOW_MODEL_NAME` único (`produccion-forecast`).
- **Código:** `model_loader.py:33-36` tiene `MODEL_NAME_BY_TARGET` con dos entradas y una env var extra `MLFLOW_MODEL_NAME_GAS`. La API sirve dos modelos (ADR-039:79, ADR-042:28).
- **Fix:** actualizar la tabla de ADR-037 a dos loaders + `MLFLOW_MODEL_NAME_GAS`.

### A5 — 🟠 `autolog` no se usa (ADR-030)

- **ADR-030:17, :28** cita *"`mlflow.sklearn` con autolog"* como razón de elegir MLflow.
- **Código:** 0 ocurrencias de `autolog`. Todo el logging es manual (`log_param`/`log_metric`/`log_model` en `ml/registry.py`).
- **Fix:** quitar "autolog" del ADR-030 (o decir "logging manual con la API de `mlflow`").

### A6 — 🟡 Métricas del campeón: dev/test, no val/test (ADR-030)

- **ADR-030:9** dice métricas *"sobre val y test"*.
- **Código:** `ml/registry.py:139-143` loguea `dev_*` y `test_*` (dev = train+val). No hay `val_*` en el run del campeón. (Los **baselines** sí loguean val/test — `ml/baseline.py`.)
- **Fix:** ADR-030 debe decir "dev y test" para el campeón.

### A7 — 🟡 Criterio de promoción: val vs test (ADR-039, ADR-034, ADR-029)

- **ADR-039:64 / ADR-034:54 / ADR-029:38** dicen *"supera a la persistencia en RMSE **en val** y lo confirma en test"*.
- **Código:** `promotion_decision()` (`ml/registry.py:44-72`) evalúa **ambas condiciones sobre test RMSE**. `val` no interviene; `train_final()` ni calcula métricas de val.
- **Fix:** alinear la redacción de los tres ADRs con el código (test), **o** cambiar el código para usar val en la primera condición. Ligado a A8.

### A8 — 🟡 "test intacto" vs "promoción en cada retrain" (ADR-039)

- **ADR-041:11 / ADR-039:92** dicen que `test` queda **intacto** / se usa "una sola vez".
- **ADR-039:64-65** dice que el criterio (que mira test) **se re-evalúa en cada reentreno**.
- Como `test` crece con cada mes nuevo (split fijo), el `test_rmse` del candidato se mide sobre **más datos** que el del Production vigente (guardado como tag) → la condición ② compara RMSE de conjuntos distintos.
- **Decisión del equipo:** promover con `val` y dejar test para evaluación única, **o** correr la ventana de test con los datos nuevos. Es una tensión de diseño real, no un typo.

### A9 — 🟡 `COMPOSE_PROFILES` en el archivo equivocado (ADR-036)

- **ADR-036:57** dice activar el perfil `ml` agregándolo a `COMPOSE_PROFILES` del **`api/.env`**.
- **Realidad:** `api/.env` es `env_file` del contenedor `api`; Compose lee `COMPOSE_PROFILES` del `.env` del *project directory* (`infra/.env`) o del shell. El deploy (`ci.yml:171`) corre `docker compose up -d` **sin `--profile`** y sin exportar nada → **el servicio `mlflow` no se levanta en el deploy**. La API queda degradada (503 salvo precómputo fresco).
- **Fix:** ADR-036 debe decir `infra/.env` (o `export` en el shell). Es también un bug operativo real.

### A10 — 🟡 Criterio de estáticas divergente en el precómputo (ADR-043)

- `forecast_precompute.py:103-106` deriva las estáticas **por exclusión**; `feature_reader.py:47-55` (la API) usa una **allowlist** de 14 columnas. El comentario del batch dice que es "el mismo criterio".
- **Efecto:** `produjo_mes_pasado` queda en el grupo "estáticas" del batch (congelado al mes base) mientras la API lo recalcula. Hoy no diverge en valores porque el motor recalcula esa columna y pisa el valor, pero es un **riesgo latente**.
- **Fix:** unificar el criterio (que el batch use la misma allowlist).

### A11 — 🟢 Comentario obsoleto en el código (no es ADR)

- `retrain.py:56-59` dice *"train.py aún no loguea a MLflow a propósito"* — falso, `--mlflow` existe y promueve (`ml/train.py:175-194`). Contradice al ADR-040:51 y al código.
- **Fix:** borrar el comentario.

### Todo lo demás del modelado: COINCIDE ✅

Verificado contra código: cortes del split, universo train-only, target por merge de calendario (no shift), `anio` excluido, `mes`=mes objetivo, las 3 reglas de baseline (naive estacional con lag **11**, correcto), las 14 categóricas, `CATEGORICAL_NA_FILL`/`OneHotDESC`, las 7 features de ADR-033, k=5 vecinos, CV temporal `time_series_folds`, `ParameterSampler`, `RMSE_SCORER`, `BEST_PARAMS` (200/24/0.5/5 y 400/16/0.5/5), `CHAMPION="random_forest"`, `selected_features` = 27/19 con las descartadas ausentes, imputación 0+flag / mediana / 0 / DESCONOCIDO, descarte de negativos antes del feature engineering, sin clip ni log1p. **Todos los nombres de tablas, env vars y comandos de infra coinciden.**

Discrepancia menor: el docstring de `selected_features` (`ml/features.py:110`) dice "el orden replica el ranking por importancia"; en realidad agrupa autorregresivas antes que estáticas. El **conjunto** es correcto, el orden no. Irrelevante para el modelo.

---

## B. ADR contradice a otro ADR (contradicciones reales)

| # | Tema | ADR A | ADR B | Fix |
|---|---|---|---|---|
| B1 | `log1p`: descartado con evidencia vs "mejora futura" | ADR-038:39,50 (descarta, 496,6 vs 242,8) | ADR-034:45 ("se evaluará") | ADR-034 debe decir "descartado, ver ADR-038" |
| B2 | Campeón | ADR-039:62 (Random Forest) | ADR-034:49 (XGBoost) | Marcar ADR-034 §1 como revisado por ADR-039 (ver D) |
| B3 | Imputación | ADR-038:18 (mediana global "miente") | ADR-034:17,40 (usa "mediana") | ADR-034 debe describir la imputación por feature de ADR-038 |
| B4 | Mueve `TRAIN_END`? | ADR-040:52 ("**No** se mueven") | ADR-032:47 y ADR-031:71 ("mueve TRAIN_END hacia adelante") | Reconciliar: ver nota abajo |
| B5 | Origen features | ADR-035:17/31, ADR-040:38 (Bronze) | ADR-033:19,21,69 (Gold) | = A2 |
| B6 | Walk-forward | ADR-034:38 (elige expanding window) | ADR-028:63 ("mejora futura") | ADR-028 debe aclarar: se refiere al split de evaluación, no al CV del tuning |
| B7 | Serving live | ADR-042:71 (RNF <5s), ADR-043 (baja latencia) | ADR-035:11,46 ("no hay requisito de latencia online") | ADR-035 debe matizar: no hay *online store*, pero sí RNF de latencia del request |
| B8 | Cadencia retrain | ADR-040:27 (solo mensual) | ADR-037:17 ("mensual o semanal") | ADR-037 debe decir "mensual" |

**Nota B4 (importante):** la contradicción es real y arrastra dos **promesas incumplidas**:
- ADR-032:47 justifica el fallback `DESCONOCIDO` diciendo que el reentreno "da columna propia a las categorías nuevas cuando ya tienen historia". Con `TRAIN_END` fijo (ADR-040:52), las 13 operadoras nuevas quedan en `DESCONOCIDO` **para siempre**.
- ADR-031:71 dice que los 1.224 pozos excluidos "se incorporan al reentrenar". Con `TRAIN_END` fijo, **nunca**.
- Junto con A1 (`RETRAIN_ASOF` inerte), esto significa que **el retrain no reentrena sobre datos distintos**. Es el hallazgo de fondo para la defensa: hoy el retrain refresca features y código, pero no incorpora meses nuevos.

---

## C. Citas fabricadas y referencias rotas

### C1 — Citas "textuales" de frases inexistentes 🟠
- **ADR-034:7** cita como textual: *"el algoritmo… se documentará en un ADR aparte"*. ADR-029:56 dice *"se documenta en ADR-034"*. La cita no existe así.
- **ADR-033:7** cita: *"el diseño de features avanzadas (lags múltiples, medias móviles, agregados por entidad)"*. Esas palabras **no están** en ADR-031.
- **ADR-040:44** cita: *"la orquestación del retrain debería reentrenar ambos modelos"*. **No está** en ADR-039.
- **ADR-036:5** dice que ADR-030 *"delegó la infraestructura al Rol 3"*. ADR-030 no menciona roles (0 hits).
- **Fix:** quitar las comillas y parafrasear, o citar la frase real.

### C2 — Referencias irresolubles
- **ADR-042:38** cita *"ADR previo: `MAX_FORECAST_DAYS`"*. Esa constante no existe en ningún ADR. → nombrar el ADR real (o quitar).
- **ADR-042:70** dice que subsume a `/predict`. `/predict` no se define en ningún ADR. → aclarar que era un endpoint de una iteración previa.
- **ADR-041:113** cita `feature_store_build.py` como "ADR-035"; ese nombre de archivo no aparece en ADR-035. → OK como ref a código, pero no es del ADR-035.
- **ci.yml:72** (no es ADR, pero cuenta): comentario cita *"ADR-044"*, que **no existe**. El correcto es ADR-042. → corregir comentario.

### C3 — Atribuciones incorrectas
- **ADR-039:44,72** atribuye a **ADR-029** un "baseline de persistencia sobre gas". ADR-029 solo documenta petróleo; los números de gas (635,5 / 457,8) solo están en 039 y 041. → ADR-029 debería agregar el baseline de gas, o ADR-039 dejar de atribuírselo.
- **ADR-041:156** lista **ADR-039 dos veces** en "Relacionados", una con la descripción *"misma selección con `prod_gas_*`"* que **contradice** a ADR-041:109 (*"los sets difieren en estructura, no es solo cambiar el prefijo"*). → corregir.
- **ADR-041:59** atribuye a ADR-031 una "limitación de serving"; ADR-031 es de dataset, no de serving. → matiz de redacción.
- **ADR-035:34** dice que el Pipeline "se serializa en MLflow (ADR-038)"; ADR-038 no menciona MLflow. → la ref correcta es ADR-040/registry.

---

## D. Revisiones sin marcar (falta "Superseded")

Ningún ADR se marca como revisado. Agregar una nota al inicio del ADR viejo:

| ADR viejo | Revisado por | Qué cambió |
|---|---|---|
| ADR-034 §1 (campeón XGBoost) | ADR-039 | Tras tuning, el campeón es Random Forest |
| ADR-033 (7 features, incl. `water_cut`, `prod_vecinos_mean`) | ADR-041 | El set final excluye las no recursion-safe; quedan 27/19 |
| ADR-034 §C (log1p "mejora futura") | ADR-038 | log1p descartado con evidencia |

Formato sugerido (una línea bajo el título):
> `> **Revisión (ADR-039):** la elección preliminar de XGBoost fue refinada a Random Forest tras el tuning. Ver ADR-039.`

**Estados:** decidir si 028, 031, 033, 034, 038 pasan a "Aceptada" (coherente con que 035/039–043 dependen de ellos) o si todos vuelven a un estado uniforme. Hoy la mezcla Propuesta/Aceptada es incoherente.

---

## E. Cifras inconsistentes entre ADRs

| Concepto | Valor A | Valor B | Correcto | Fix |
|---|---|---|---|---|
| RF petróleo val RMSE | **227,1** (ADR-039:26) | **227,3** (ADR-041:122) | el modelo servido es el de 27 features: **227,5** (ADR-041:121) | Unificar en 039 al valor del set final |
| RF gas val RMSE | 580,4 (ADR-039:35) | 580,1 (ADR-041:127) | idem gas: **576,6** (27→19, ADR-041:126) | Unificar |
| Pozos universo | 4.281 (ADR-028:19,47) | 3.018 (ADR-031:13,43) | 3.018 (post train-only) | ADR-031:43 dice "(igual universo que el EDA)" — **falso**, el EDA da 4.281. Corregir. Y 4.281−1.224 = 3.057 ≠ 3.018: revisar la resta. |
| Filas | 347.063 (ADR-028:66) | 319.554 (ADR-031:13) | 319.554 | La brecha (27.509) no la explican las "6 filas negativas". Aclarar. |
| Proporciones split | 80,5/19,5 dev/test (ADR-028:75) | conteos reales → 85,0/15,0 (ADR-031:14) | 85,0/15,0 | ADR-028 dice "clavan las proporciones pedidas" — no las clavan sobre el dataset real. Corregir. |
| % ceros | 35,5% (ADR-028:17) | ~25% (ADR-031:15, ADR-033, ADR-034) | ambos, en contextos distintos | ADR-028:47 usa el 35% para justificar el filtro de universo; tras el filtro quedan 25%. Aclarar que el filtro saca ~10pp, no 35. |
| Volumen store | ~6k filas (ADR-035:11, ADR-041:148) | ~320k (= 319.554) | ~320k | Error de ~50×, y sostiene el descarte de Feast. Corregir la cifra (el argumento de descarte sigue en pie con 320k, pero hay que reescribirlo). |
| Rango test | 2024-12→2026-04 (ADR-028:73) | features hasta 2026-03 (ADR-031:13) | revisar | Inconsistencia de 1 mes en el borde. |

**Cifras que SÍ cierran** (no tocar): persistencia val 250,6 / test 166,2; gas val 635,5 / test 457,8; test final 157,5 y 409,2; BEST_PARAMS; Ridge 239,0 / XGBoost 236,9; 6 filas negativas; 13 empresas / 10,4%; corr 0,95; 79% vs 64%; día 6 vs día 5.

---

## F. Plan de corrección por archivo

Agrupado por dueño de rol para repartir.

### Micol (Rol 1 — modelado): ADR-028, 029, 031, 032, 033, 034, 038, 041
- **ADR-034:** marcar §1 como revisado por ADR-039 (campeón RF); corregir §C (log1p descartado, ADR-038); corregir "imputación por mediana" → por feature; quitar cita textual falsa de :7. **(A5? no — B1,B2,B3,C1)**
- **ADR-033:** corregir "Gold" → "Bronze" (A2); nota de que `water_cut`/`prod_vecinos_mean` quedaron fuera por ADR-041; quitar cita textual falsa de :7 (C1).
- **ADR-041:** arreglar lista de "Relacionados" (ADR-039 duplicado + descripción contradictoria, C3); corregir cifra "~6k filas" (E); corregir "227,3"→ dejar claro cuál es el servido (E).
- **ADR-031:** corregir "(igual universo que el EDA)" y la resta de pozos (E); explicar la brecha de filas (E); reconciliar la promesa "mover TRAIN_END" con ADR-040 (B4).
- **ADR-028:** corregir proporciones del split (E); aclarar el 35% vs 25% (E); aclarar walk-forward (B6); revisar borde de test (E).
- **ADR-032:** reconciliar la promesa "mueve TRAIN_END" con ADR-040 (B4).
- **ADR-029:** agregar baseline de gas o dejar de atribuírselo ADR-039 (C3).

### Valentino (Rol 2 — store + orquestación): ADR-035, 040
- **ADR-040:** **decisión** sobre `RETRAIN_ASOF` (A1: arreglar código o corregir ADR); quitar cita textual falsa de :44 (C1).
- **ADR-035:** corregir "Gold"→"Bronze" (A2); corregir "~6k filas" (E); matizar "sin requisito de latencia" (B7); corregir ref a ADR-038 por serialización (C3).

### Tomás (Rol 3 — serving + infra): ADR-030, 036, 037, 039, 042, 043
- **ADR-037:** actualizar a dos modelos + `MLFLOW_MODEL_NAME_GAS` (A4).
- **ADR-039:** quitar la afirmación `prod_pet`/`water_cut` en gas (A3); unificar cifras 227/580 (E); redacción del criterio de promoción val→test (A7).
- **ADR-030:** quitar "autolog" (A5); "val/test"→"dev/test" para el campeón (A6).
- **ADR-036:** corregir `api/.env`→`infra/.env` para `COMPOSE_PROFILES` (A9).
- **ADR-042:** nombrar el "ADR previo"/`MAX_FORECAST_DAYS` y aclarar `/predict` (C2).
- **ADR-043:** unificar criterio de estáticas del batch con la allowlist de la API (A10).

### Código (cualquiera)
- `retrain.py:56-59`: borrar comentario obsoleto (A11).
- `ci.yml:72`: "ADR-044"→"ADR-042" (C2).
- **Si se elige arreglar A1:** aplicar `RETRAIN_ASOF` en `feature_store_build.py` y propagarlo desde `features_refrescadas`.
- **Si se elige arreglar A10:** que el batch use `feature_reader.STATIC_FEATURE_COLUMNS`.

---

## G. Decisiones que son del equipo (no las tomo yo)

Estas cambian comportamiento o arquitectura; requieren que decidan ustedes:

1. **A1 — `RETRAIN_ASOF`:** ¿arreglar el código para que recorte (respeta la intención) o documentar que hoy no recorta? Ligado a que el retrain no aprende de datos nuevos.
2. **B4 — split fijo:** ¿el retrain debe avanzar `TRAIN_END` (aprender de datos nuevos) o se mantiene fijo por reproducibilidad? Hoy los ADRs prometen las dos cosas.
3. **A7/A8 — promoción:** ¿promover con val (y test una vez) o mantener test-en-cada-retrain? Afecta la validez estadística.
4. **A2 — Bronze/Gold:** ¿el store debe leer de Gold (pasa por DQ) o se documenta que lee de Bronze a propósito?
5. **Estados:** ¿todos "Aceptada" o esquema uniforme?

---

*Generado por auditoría cruzada. Cada hallazgo tiene archivo:línea verificable. Los marcados 🔴/🟠 son los que la cátedra puede encontrar cruzando ADRs con el código.*
