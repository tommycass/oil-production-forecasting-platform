# Título: ADR-031: Construcción del dataset de modelado y prevención de leakage temporal

**Estado:** Propuesta

## Contexto

El ADR-028 fijó el **encuadre** del problema (regresión tabular global, target `prod_pet` del mes siguiente, universo petrolero, split temporal de 3 vías). Lo que faltaba decidir y documentar es **cómo se construye concretamente** el frame `(features, target)` que come el modelo, de forma que **ningún feature use información del mes que se predice** (leakage futuro→pasado) ni de los conjuntos de validación/test (leakage val/test→train).

La construcción vive en `ml/dataset.py` (`build_basic_dataset`) y se exploró/validó en `notebooks/03_feature_engineering.ipynb`. Este ADR registra las decisiones de procesamiento y el resultado de la **auditoría de leakage** que se hizo sobre el dataset generado.

### Evidencia del EDA / construcción

- Dataset básico resultante: **319.560 filas**, **3.018 pozos**, rango de meses de features **2006-01 → 2026-03**.
- Split (por mes de los features): **train 223.621 / val 47.883 / test 48.056**.
- El **~25%** de los targets (`y_next`) es **0** (meses de pozos petroleros parados): predecir 0 es parte del problema.
- Las medidas de producción son muy asimétricas (cola larga + masa en 0), confirmado en los histogramas del notebook.

## Decisión

### 1. Encuadre temporal: features del mes `t`, target del mes `t+1`

Cada fila es `(pozo, mes t)`. **Todos los features corresponden al mes `t`** y el target `y_next` es `prod_pet` del **mes `t+1`**.

- Por construcción, ningún feature usa información del mes que se predice. `prod_pet` del mes `t` entra como feature (= "producción de petróleo del mes anterior"), igual que `prod_agua`, `prod_gas`, `tef`, etc. del mes `t`.
- `anio`/`mes` quedan como el mes de los features (`t`); para estacionalidad se recomienda codificar `mes` de forma cíclica (sin/cos) en el modelado.

**Alternativa descartada:** usar features del mismo mes que el target → leakage directo (no se conoce la producción del mes a predecir al momento de predecir).

### 2. Target por *merge* de calendario, no por `shift(-1)` de filas

El target se arma uniendo el panel consigo mismo desplazado un mes (`periodo + 1 mes`, *inner join*), **no** con un `shift(-1)` por fila.

- **Motivo:** si un pozo tiene un **hueco** en su serie mensual, un `shift(-1)` por fila tomaría el siguiente mes *disponible* (t+k con k≠1) como si fuera t+1, generando un par feature/target con horizonte equivocado. El *merge* por calendario garantiza que `y_next` sea **siempre exactamente** el mes siguiente; las filas sin mes siguiente real se descartan.
- Verificado en el notebook: el 100% de los pares tienen gap = 1 mes y `y_next` coincide con el `prod_pet` real del mes objetivo.

> Nota: el `add_target` previo de `ml/dataset.py` (usado por `build_modeling_frame`) usa `shift(-1)`; queda como deuda a alinear si ese pipeline se sigue usando.

### 3. Universo petrolero definido **solo con train** (refina ADR-028 §3)

El universo de pozos (con `prod_pet > 0` en algún mes) se calcula **únicamente sobre train** (`periodo ≤ TRAIN_END`), no sobre todo el histórico.

- **Motivo (anti-leakage de selección):** definir el universo sobre todo el histórico hace que la *pertenencia* de un pozo dependa de datos de val/test (un pozo que recién produce petróleo en 2025 entraría con todas sus filas, incluidas las de train). La auditoría encontró **1.224 pozos** cuyo primer `prod_pet > 0` es posterior a `TRAIN_END` (1.287 filas que entrarían a train indebidamente). Restringir a train los elimina y deja **3.018 pozos** (igual universo que el EDA).
- **No** filtra los meses en 0 de pozos petroleros: un pozo parado sigue siendo una fila válida con target 0. Solo deja afuera pozos que **nunca** son petroleros (gas/inyección, oil ≡ 0), que no son el objetivo del forecast.

**Alternativa descartada:** universo sobre todo el histórico (criterio de ADR-028 y de `load_production`) → leakage de selección.

### 4. Selección de columnas candidatas a feature

A partir del EDA se clasificó cada columna cruda en `input` / `feature_engineering` / `descartar` (curado en `ml.eda.MODEL_INPUT_ROLE`):

- **22 candidatas a input**: 6 numéricas (`prod_gas`, `prod_agua`, `tef`, `profundidad`, `coordenadax`, `coordenaday`), 2 temporales (`anio`, `mes`) y 14 categóricas; más `prod_pet` que entra como lag.
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
- El universo train-only **no predice pozos que recién aparecen en val/test** (~1.224 pozos quedan fuera). Es el costo correcto de no usar el futuro para seleccionar; pozos nuevos se incorporan al reentrenar (mover `TRAIN_END`).
- Queda una **inconsistencia** entre `build_basic_dataset` (universo train-only, target por merge) y el `build_modeling_frame`/`load_production` heredados (universo full-history, target por `shift`); alinearlos es deuda técnica.
- `anio` como feature implica cuidado con la **extrapolación** (predecir años fuera del rango de train).

---

> Relacionados: **ADR-028** (encuadre del problema y split), **ADR-032** (encoding de categóricas), **ADR-029** (baselines). El diseño de features avanzadas (lags múltiples, medias móviles, agregados por entidad) se documentará al avanzar el modelado.
