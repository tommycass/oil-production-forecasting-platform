# Título: ADR-031: Construcción del dataset de modelado y prevención de leakage temporal

**Estado:** Aceptada

## Contexto

El ADR-028 fijó el **encuadre** del problema (regresión tabular global, target `prod_pet` del mes siguiente, universo petrolero, split temporal de 3 vías). Lo que faltaba decidir y documentar es **cómo se construye concretamente** el frame `(features, target)` que come el modelo, de forma que **ningún feature use información del mes que se predice** (leakage futuro→pasado) ni de los conjuntos de validación/test (leakage val/test→train).

La construcción vive en `ml/dataset.py` (`build_basic_dataset`). Este ADR registra las decisiones de procesamiento y el resultado de la **auditoría de leakage** que se hizo sobre el dataset generado.

### Evidencia del EDA / construcción

- Dataset básico resultante: **319.554 filas** (tras descartar 6 filas con producción negativa, ADR-038), **3.018 pozos**, rango de meses de features **2006-01 → 2026-03**.
- Split (por mes de los features): **train 223.615 / val 47.883 / test 48.056**.
- El **~25%** de los targets (`y_next`) es **0** (meses de pozos petroleros parados): predecir 0 es parte del problema.
- Las medidas de producción son muy asimétricas (cola larga + masa en 0), confirmado en los histogramas del notebook.

## Decisión

### 1. Encuadre temporal: features del mes `t`, target del mes `t+1`

Cada fila es `(pozo, mes t)`. **Las medidas corresponden al mes `t`** y el target `y_next` es `prod_pet` del **mes `t+1`**.

- Por construcción, ninguna medida usa información del mes que se predice. `prod_pet` del mes `t` entra como feature (= "producción de petróleo del mes anterior"), igual que `prod_agua`, `prod_gas`, `tef`, etc. del mes `t`.
- **Excepción — calendario:** `mes` corresponde al **mes objetivo (`t+1`)**, no al mes de las medidas. La fecha del mes a predecir se conoce de antemano (es determinística), así que **no es leakage** y es la señal útil: captura la estacionalidad del mes que se pronostica, no la del mes anterior. Hoy `mes` entra como **entero** (passthrough en el `Pipeline`); codificarlo de forma **cíclica** (sin/cos) queda como posible mejora.
- **`anio` se excluye como feature.** Sus valores en val/test (2024–2026) caen **fuera del rango de train** (≤2023) → extrapolación, problemática sobre todo para modelos de árboles (que no extrapolan). El criterio no es la ciclicidad (`profundidad`/coords tampoco lo son y se usan), sino el rango fuera de muestra; además la tendencia macro que aportaría `anio` ya la captura el lag de `prod_pet`. Se sigue cargando solo para construir `periodo`.

**Alternativa descartada:** usar las medidas del mismo mes que el target → leakage directo (no se conoce la producción del mes a predecir al momento de predecir). En cambio el calendario (`mes`) del target sí es conocido y se usa.

### 2. Target por *merge* de calendario, no por `shift(-1)` de filas

El target se arma uniendo el panel consigo mismo desplazado un mes (`periodo + 1 mes`, *inner join*), **no** con un `shift(-1)` por fila.

- **Motivo:** si un pozo tiene un **hueco** en su serie mensual, un `shift(-1)` por fila tomaría el siguiente mes *disponible* (t+k con k≠1) como si fuera t+1, generando un par feature/target con horizonte equivocado. El *merge* por calendario garantiza que `y_next` sea **siempre exactamente** el mes siguiente; las filas sin mes siguiente real se descartan.
- Verificado en el notebook: el 100% de los pares tienen gap = 1 mes y `y_next` coincide con el `prod_pet` real del mes objetivo.

> Resuelto: el pipeline heredado `build_modeling_frame` (universo full-history + target por `shift(-1)`) se **retiró** de `ml/dataset.py`. `baseline.py` y el modelado usan ahora únicamente `build_basic_dataset` (universo train-only + target por merge), por lo que baselines y modelos son comparables sobre el mismo dataset.

### 3. Universo petrolero definido **solo con train** (refina ADR-028 §3)

El universo de pozos (con `prod_pet > 0` en algún mes) se calcula **únicamente sobre train** (`periodo ≤ TRAIN_END`), no sobre todo el histórico.

- **Motivo (anti-leakage de selección):** definir el universo sobre todo el histórico hace que la *pertenencia* de un pozo dependa de datos de val/test (un pozo que recién produce petróleo en 2025 entraría con todas sus filas, incluidas las de train). La auditoría encontró **1.224 pozos** cuyo primer `prod_pet > 0` es posterior a `TRAIN_END` (1.287 filas que entrarían a train indebidamente). Restringir el universo a `periodo <= TRAIN_END` los elimina y deja **3.018 pozos** con historia petrolera en train.
- **No** filtra los meses en 0 de pozos petroleros: un pozo parado sigue siendo una fila válida con target 0. Solo deja afuera pozos que **nunca** son petroleros (gas/inyección, oil ≡ 0), que no son el objetivo del forecast.

**Alternativa descartada:** universo sobre todo el histórico (criterio de ADR-028 y de `load_production`) → leakage de selección.

### 4. Selección de columnas candidatas a feature

A partir del EDA se clasificó cada columna cruda en `input` / `feature_engineering` / `descartar` (curado en `ml.eda.MODEL_INPUT_ROLE`):

- **Candidatas a input**: 6 numéricas (`prod_gas`, `prod_agua`, `tef`, `profundidad`, `coordenadax`, `coordenaday`), las temporales (`anio`, `mes`) y 14 categóricas; más `prod_pet` que entra como lag. En el dataset final se **excluye `anio`** (ver §1), quedando `mes` como única feature de calendario.
- **`feature_engineering`** (no entran crudas, sirven de clave): `idpozo`, `idempresa`, `idarea*`.
- **Descartadas**: identificadores legibles (`sigla`), metadata de carga (`fechaingreso`, `fecha_data`, `idusuario`, `rectificado`, `habilitado`, `observaciones`), casi vacías (`vida_util`, `observaciones`), constantes/casi-constantes (`iny_co2`, `iny_otro`, `tipo_de_recurso`, `iny_agua`, `iny_gas`).

### 5. Split etiquetado por `periodo` (mes de los features)

Se reusa el criterio de fechas del ADR-028 (`TRAIN_END`, `VAL_END`) etiquetando por el mes de los features.

- La auditoría detectó que las filas del borde (features en `TRAIN_END`) tienen su target en el período siguiente. **Se concluyó que esto NO es leakage**: los targets de train (≤ ago-2023) y de val (≥ sep-2023) **no se solapan** y train nunca ve un target de val/test; que una observación de borde sea label de train y feature de val es la operatoria normal de un forecast (al predecir, conocer el pasado es legítimo). Por eso se mantiene el split por `periodo`.

## Consecuencias

**Positivas:**
- Dataset **reproducible y leak-free** en las dos direcciones críticas (futuro→pasado y val/test→train), auditado con datos.
- El *merge* de calendario hace el target **robusto a huecos** en las series.
- El universo train-only elimina el leakage de selección y alinea el dataset con el universo del EDA.
- Persistido como CSV local en `data/processed/` (gitignoreado): derivado reproducible que no toca el crudo ni se versiona.

**Negativas / trade-offs:**
- El universo train-only **no predice pozos que recién aparecen en val/test** (~1.224 pozos quedan fuera). Es el costo correcto de no usar el futuro para seleccionar el universo. Se **incorporan al reentrenar**: con los cortes derivados (ADR-028 Revisión jul-2026) la ventana de train se corre con la fecha de reproceso (`asof`/`max(periodo)`), así los pozos que ya acumularon historia entran al universo en corridas posteriores.
  - **Reproceso por fecha:** `build_basic_dataset` acepta `asof` (de la env var `RETRAIN_ASOF`, ver `ml.config.retrain_asof`) y recorta `periodo <= asof` **antes** de calcular universo y features, así un reentreno "como si fuera el día X" no usa datos posteriores (mismo principio anti-leakage aplicado en el tiempo). Detalle de orquestación en **ADR-040**.
- Para mantener la coherencia hubo que **retirar** el pipeline heredado (`build_modeling_frame`/`load_production`, universo full-history + target por `shift`) y realinear `baseline.py`: las cifras de baseline del ADR-029 se recalculan sobre el dataset unificado.
- Al excluir `anio`, el modelo no tiene una feature de **tendencia macro** explícita; se asume que el lag de `prod_pet` la captura. Si el modelado mostrara una tendencia no capturada, la vía correcta es una feature de **antigüedad/elapsed-time del pozo** (dentro de rango), no el año calendario.

---

> Relacionados: **ADR-028** (encuadre del problema y split), **ADR-032** (encoding de categóricas), **ADR-029** (baselines), **ADR-033** (diseño de las features derivadas: lags, ventanas, vecinos), **ADR-034** (algoritmo y validación temporal).
