# Título: ADR-028: Diseño del problema predictivo y estrategia de validación temporal
**Estado:** Propuesta

## Contexto

La Fase 3 integra un **modelo predictivo** a la plataforma. El endpoint `/forecast` hoy devuelve datos mock (ADR-009) y la capa Gold ya expone `gold.fact_produccion_mensual` con grano (pozo, mes). Antes de entrenar cualquier modelo, hay que decidir y documentar **cómo se encuadra el problema en términos de Machine Learning**: qué se predice, sobre qué universo de datos, con qué métrica se evalúa y —sobre todo— cómo se separan los datos para validar sin filtrar información del futuro (*leakage* temporal).

Estas decisiones son transversales: condicionan el feature store, el script de entrenamiento, el registry y la API de inferencia. Por eso se fijan en un único ADR de diseño antes de arrancar la implementación.

### Evidencia del EDA

El análisis exploratorio (`notebooks/01_outliers_correlaciones.ipynb`, sobre `data/_explore/produccion_full.csv`) arrojó:

- **4.929 pozos**, rango **2006–2026** (244 meses); mediana de **~80 meses de historia por pozo**. El **91%** de los pozos tiene ≥12 meses y el **81%** ≥24 meses.
- `prod_pet` está **muy sesgada**: mediana **51 m³**, máximo **26.593**, desvío **1.105**. El **35,5%** de los registros es **cero**.
- Los ceros no son ruido: el dataset **mezcla tipos de pozo**. Solo `Petrolífero` (85,8% de meses con producción) y parte de `Gasífero` producen petróleo; `Inyección` y `Sumidero` no producen (~0%).
- **4.281 pozos** tienen al menos un mes de `prod_pet > 0` (universo petrolero), y de esos el 81% tiene ≥24 meses.
- `tipo_de_recurso` es **constante** (un solo valor, "NO CONVENCIONAL").
- El volumen de datos **crece fuerte en el tiempo** (2006: ~2.000 registros/año → 2025: ~47.000) y **2026 está incompleto** (datos hasta abril).

## Decisión

### 1. Encuadre de ML: regresión tabular supervisada global (horizonte t+1)

**Alternativas consideradas:**
- **Forecasting clásico por serie (ARIMA, Prophet), un modelo por pozo:** modela bien cada serie pero **no escala** a 4.281 pozos (un modelo por pozo), no aprovecha atributos del pozo (formación, cuenca) ni el aprendizaje cruzado entre pozos.
- **Modelos secuenciales (LSTM/RNN):** potentes para series, pero con costo de implementación, datos y cómputo desproporcionado para el alcance del TP.
- **Regresión tabular global (elegida):** cada fila (pozo, mes) es un ejemplo; el target es la producción del **mes siguiente**; **un único modelo** aprende de todos los pozos usando features autoregresivas (lags) + atributos.

**Decisión:** encuadrar el problema como **regresión supervisada tabular con un modelo global**, horizonte de **1 mes (t+1)**. Escala a miles de pozos, aprovecha el feature store, usa atributos estáticos y permite que un pozo con poca historia se beneficie de patrones aprendidos en otros.

### 2. Target y grano

**Alternativas:** `prod_pet` vs `prod_gas`; grano (pozo, mes) vs (yacimiento, mes); horizonte t+1 vs multi-mes.

**Decisión:** target = **`prod_pet` (m³ de petróleo) del mes siguiente**, grano **(pozo, mes)**.
- **`prod_pet`** alinea con el endpoint `/forecast` y con la narrativa de la plataforma, aunque el EDA muestre más actividad gasífera (`prod_gas` poblado en 79% vs 64%). Queda registrado como decisión de **alcance**.
- **Grano pozo:** el EDA confirma historia suficiente por pozo, por lo que **no hace falta agregar a yacimiento** (que se reserva como plan B si un subconjunto resultara demasiado ralo).
- **Horizonte t+1** para empezar simple; ampliar el horizonte queda como trabajo futuro.

### 3. Universo de entrenamiento: pozos petroleros

**Alternativas:** entrenar con todos los pozos / filtrar por `tipopozo = 'Petrolífero'` / filtrar por producción observada.

**Decisión:** restringir a **pozos petroleros**, definidos como los que tienen **al menos un mes de `prod_pet > 0`** (4.281 pozos). Incluir pozos de inyección/sumidero/gasíferos puros metería un 35% de ceros estructurales que no corresponden al fenómeno a modelar. El criterio por producción observada es más robusto que confiar solo en la etiqueta `tipopozo` (que tiene nulos).

### 4. Tratamiento del target y métrica de evaluación

**Alternativas de métrica:** MAE / RMSE / MAPE / sMAPE. **Alternativas de target:** crudo / `log1p` / con capeo de outliers.

**Decisión:**
- **Métrica principal: RMSE** (raíz del error cuadrático medio, en m³): penaliza los errores grandes, que es lo que importa en un target de **cola pesada** donde los pozos de mayor producción concentran el error y **son la señal a captar** (coherente con ADR-039). Se reportan **R²** (comparable entre períodos) y **MAE** (referencia interpretable) en paralelo. Se **descarta MAPE/sMAPE** por la gran proporción de ceros y valores chicos, que las vuelven inestables.
  > La selección de modelo/hiperparámetros se hace por **RMSE en val** (ADR-029/034); el baseline reporta además MAE (ADR-029).
- Dado el fuerte sesgo de `prod_pet`, se **evaluó** transformar el target con **`log1p`** y/o capear outliers extremos; la evidencia (ADR-039) mostró que **en RMSE los extremos son señal**, así que el target se deja en **escala original** (sin transformar ni capear).

### 5. Estrategia de validación temporal (split)

**Alternativas:**
- **Split aleatorio:** ❌ inválido en series temporales — mezcla fechas y produce *leakage* (el modelo "ve el futuro").
- **Split temporal simple (train/test):** correcto pero no deja un conjunto de **validación** para elegir modelo/hiperparámetros sin tocar test.
- **Validación walk-forward / ventana expansiva:** la más robusta, pero más costosa de implementar; se deja como mejora futura.
- **Split temporal de 3 vías, global por fecha (elegida):** dev (train+val) y test separados por fecha, y dentro de dev otro corte temporal train/val.

**Decisión:** split **temporal de 3 vías**, con **corte global por fecha** (todos los pozos comparten el mismo límite temporal, para que nunca se use el futuro de un pozo al predecir otro), en proporciones **0,8/0,2 dev/test** y **0,8/0,2 train/val dentro de dev**. Cortes (sobre el universo petrolero, 347.063 registros):

| Conjunto | Rango temporal | % del total | Rol |
|---|---|---|---|
| **train** | 2006-01 → 2023-07 | 65,0% | ajustar el modelo |
| **val** | 2023-08 → 2024-11 | 15,6% | elegir modelo/hiperparámetros |
| **dev** (train+val) | 2006-01 → 2024-11 | 80,5% | — |
| **test** | 2024-12 → 2026-04 | 19,5% | estimación final, intacto |

Resultado: dev/test = **80,5/19,5** y train/val (dentro de dev) = **80,7/19,3**. Se eligieron cortes que **clavan las proporciones pedidas** y dejan ventanas de val (~16 meses) y test (~17 meses) que **superan los 12 meses**, cubriendo un ciclo estacional completo. Los cortes son **fechas fijas**, por lo que el split es **reproducible**.

> **Nota:** 2026 está incompleto (datos hasta abril) y cae en *test*; es aceptable porque es el período más reciente y real, pero se documenta explícitamente.

## Consecuencias

**Positivas:**
- Encuadre **escalable** (un modelo global) y coherente con el feature store y con `/forecast`.
- Validación **sin leakage temporal** y con un conjunto de test intacto para una estimación honesta del error.
- Todas las decisiones quedan **ancladas en evidencia del EDA**, no en supuestos.
- Métrica de selección (**RMSE**, con R² y MAE de apoyo) acorde a lo que importa operativamente: acertar en los pozos de mayor producción.

**Negativas / trade-offs:**
- El target sesgado obliga a **cuidar transformación y métrica**; un modelo ingenuo sobre el target crudo puede dominar por outliers.
- Restringir al universo petrolero **deja afuera el gas** (decisión de alcance); si el equipo quisiera pronosticar gas, habría que revisar este ADR.
- El split por volumen concentra el test en una **ventana reciente y corta** en el tiempo (aunque amplia en registros); mitigado porque cubre >12 meses. Una validación **walk-forward** sería más robusta y queda como mejora futura.
- `prod_pet` como target fija el alcance; `tipo_de_recurso` se **descarta como feature** por ser constante.

---

> Decisiones relacionadas que se documentarán en ADRs aparte: **algoritmo concreto** (lineal vs. árboles vs. boosting), **plataforma de tracking de experimentos** (MLflow vs. Weights & Biases) y el diseño de **features** y del **feature store**.
