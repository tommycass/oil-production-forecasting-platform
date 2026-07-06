# Título: ADR-039: Preprocesamiento de datos (NaN por feature, outliers y errores)

**Estado:** Propuesta

## Contexto

El dataset de modelado (ADR-031/033) tiene **valores faltantes** y **valores extremos** que hay que tratar antes de entrenar:

- **NaN:** las features de historia (`prod_pet_lag12`, `prod_pet_acum6`, `prod_pet_roll3`, `prod_pet_delta1`, vecinos) son NaN en los primeros meses de cada pozo (p. ej. `prod_pet_lag12` es NaN en ~16% de train); algunas numéricas crudas también pueden faltar. La regresión (Ridge) y Random Forest **no aceptan NaN**.
- **Outliers:** la producción es muy asimétrica (cola larga), y hay **errores de dato** (producción negativa, físicamente imposible: `prod_gas` llegaba a −12,27).

Todo lo que se decida debe ser **anti-leakage**: los estadísticos (medianas, vocabularios) se computan **solo sobre train / el train de cada fold** (ADR-034), nunca sobre val/test ni sobre el futuro. La implementación vive en `ml/preprocessing.py` dentro del `Pipeline` (salvo el descarte de errores, que es a nivel dataset).

## Análisis de Alternativas

### 1. Imputación de NaN

- **Uniforme (mediana global para todas las numéricas):** simple, pero **miente** en las features de historia: un `prod_pet_lag12` inexistente (pozo nuevo) no es "la mediana de producción", es "no había historia".
- **Eliminar filas con NaN:** perdería los **primeros meses de cada pozo** (decenas de miles de filas), inviable y sesgado hacia pozos viejos.
- **Por feature (elegido):**
  - **Volúmenes y variación** (`prod_pet`, `prod_gas`, `prod_agua`, lags/ventanas, `delta1`): imputar **0 + flag `*_isna`** (faltante = "no hay historia"; el flag deja que el modelo lo distinga del 0 real).
  - **Físicas estáticas y operativa** (`profundidad`, coords, `tef`): **mediana de train** (son atributos con un valor típico).
  - **Ratios/flags** (`water_cut`, `produjo_mes_pasado`): **0**.
  - **Categóricas**: categoría **`DESCONOCIDO`** (ADR-032).

  Evidencia: el esquema por feature dio **ridge val RMSE 242,8** vs **~244** con mediana uniforme — leve mejora y conceptualmente más correcto.

### 2. Tratamiento de outliers

Se midió el efecto sobre la **regresión lineal** (los árboles son invariantes a transformaciones monótonas, así que clip/log no los cambian):

| Preprocesamiento de volúmenes | ridge val RMSE |
|---|---|
| `log1p` | 496,6 |
| clip percentil 1–99 (vol + tef + delta) | 277,5 |
| clip solo en tef/delta (vol sin tocar) | 245,3 |
| **sin clip (solo imputación)** | **242,8** |

- **`log1p` (descartado):** rompe la relación casi lineal `prod_pet(t) ≈ prod_pet(t+1)` (la señal dominante) → arruina el modelo lineal. Neutro para árboles.
- **Clip / winsorize (descartado):** en **RMSE** sobre un target de **cola pesada**, los **pozos grandes dominan el error** y su producción extrema **es la señal** para predecir su producción futura; al recortarla, el lineal no los distingue y el RMSE empeora. Neutro para árboles.
- **Ninguno (elegido):** los extremos de producción son **señal, no ruido**; no se clippean ni se transforman.

### 3. Errores de dato (producción negativa)

- **Imputar/clipar a 0:** mezcla un error de dato con un 0 legítimo (pozo parado).
- **Descartar las filas (elegido):** la producción es físicamente ≥ 0; los negativos son errores. Se descartan en `build_basic_dataset` **antes** del feature engineering (para no contaminar lags) — son **6 filas**.

### 4. Transformación del target

- **`log1p(target)` (descartado):** misma lógica que los outliers de features — en RMSE los pozos grandes son la señal; transformar el target optimizaría otra pérdida y empeoraría el RMSE en escala original (la métrica del ADR-028). El target se deja en **escala original**.

## Decisión

1. **Imputación de NaN por feature** (tabla §1): 0 + flag en volúmenes/variación, mediana de train en físicas/operativa, 0 en ratios, `DESCONOCIDO` en categóricas.
2. **Sin tratamiento de outliers** en features ni target: los extremos de producción son señal para el RMSE.
3. **Descartar** las filas con producción negativa (errores de dato).
4. **Dónde vive cada cosa (anti-leakage):** la imputación + flags + one-hot van en el `Pipeline` (`ml/preprocessing.build_preprocessor`), que la CV **reajusta por fold**; el descarte de negativos es a nivel dataset (es limpieza de errores, no un estadístico aprendido).

## Consecuencias

**Positivas:**
- Imputación **honesta** (faltante ≠ valor típico) y **leak-free** (fit por fold), con leve mejora empírica.
- No se pierde señal de los pozos grandes (los que más pesan en el RMSE).
- Errores de dato fuera del entrenamiento sin contaminar las features de historia.

**Negativas / trade-offs:**
- Imputar lags con 0 introduce un **sesgo** (un pozo nuevo "parece" no haber producido); se mitiga con el flag `*_isna`, pero el modelo aún debe aprender a usarlo.
- Al **no tratar outliers**, el modelo queda **expuesto a valores extremos espurios** que no sean negativos (p. ej. un pico por error de carga); se delega su detección al **data quality de Fase 2** (ADR-016) y al monitoreo, no al preprocesamiento.
- Los flags de faltante **agregan columnas** (una por feature de historia), costo menor.

---

> Relacionados: **ADR-031/033** (dataset y features que se preprocesan), **ADR-032** (one-hot con `DESCONOCIDO`), **ADR-034** (CV temporal: por qué el preprocesamiento va en el `Pipeline`), **ADR-040** (modelo a producción que consume este preprocesamiento).
