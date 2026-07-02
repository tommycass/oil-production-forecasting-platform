# Título: ADR-033: Feature engineering para el forecast (features derivadas anti-leakage)

**Estado:** Propuesta

## Contexto

El ADR-031 dejó el dataset básico con las **medidas crudas del mes t** (`prod_pet`, `prod_gas`, `prod_agua`, `tef`, `profundidad`, coords) y el calendario del target (`mes`), y **prometió** documentar aparte *"el diseño de features avanzadas (lags múltiples, medias móviles, agregados por entidad)"*. Este ADR registra ese diseño.

El objetivo es darle al modelo señal **predictiva y leak-free** más allá del último valor observado. El EDA (`notebooks/01_outliers_correlaciones.ipynb`) mostró que la producción es fuertemente autocorrelacionada (corr 0,95 con el mes siguiente) y que declina con el tiempo: hay estructura temporal (nivel, tendencia, estacionalidad, agotamiento) y espacial (pozos vecinos de un mismo reservorio) que conviene capturar.

Las features se implementan **una función por feature** en `ml/features.py` y se agregan en `build_basic_dataset` (`ml/dataset.py`) **antes** del merge del target. Se validaron en `notebooks/02_feature_engineering.ipynb`.

Doble restricción anti-leakage (igual que ADR-031):
- **Futuro→pasado:** una feature de la fila del mes t solo puede usar datos de t o anteriores.
- **Val/test→train:** ninguna feature puede aprender parámetros (medias, escalas, encoders) sobre el dataset completo; si lo hiciera, información de val/test entraría al cálculo de filas de train.

## Features elegidas

Cada fila es `(pozo, mes t)`; el target es `prod_pet(t+1)`. Todas las columnas de origen salen de la capa **Gold** (`fact_produccion_mensual` para las medidas mensuales; `dim_pozo` para los atributos estáticos como coordenadas) — el contrato exacto se define al materializarlas en el feature store.

| Feature | Columna(s) Gold | Cálculo (sobre la serie del pozo) | Qué captura |
|---|---|---|---|
| `prod_pet_roll3` | `prod_pet` | media de {t, t-1, t-2} | nivel reciente suavizado (menos ruido que el último mes) |
| `prod_pet_delta1` | `prod_pet` | `prod_pet(t) − prod_pet(t-1)` | tendencia / declinación mes a mes |
| `prod_pet_lag12` | `prod_pet` | `prod_pet(t-12)` | estacionalidad anual (mismo mes, año anterior) |
| `prod_pet_acum6` | `prod_pet` | suma de {t … t-5} | producción acumulada de la ventana (volumen reciente) |
| `water_cut` | `prod_agua`, `prod_pet` | `agua / (agua + petróleo)` en t (0 si no produjo) | madurez/agotamiento del pozo (indicador físico) |
| `produjo_mes_pasado` | `prod_pet` | `1` si `prod_pet(t) > 0`, si no `0` | pozo activo vs parado (≈25% de los meses son 0) |
| `prod_vecinos_mean` | `prod_pet`, `coordenadax`, `coordenaday` | media de `prod_pet(t)` de los 5 pozos más cercanos (excluido él mismo) | dinámica del área / reservorio compartido |

Las 5 pedidas (lag/ventanas, media móvil, estacional, delta, vecinos espaciales) más las dos extra (acumulada en ventana y "produjo el mes pasado").

## Análisis de Alternativas

### 1. Cálculo de lags: `shift` de filas vs merge por calendario

- **`shift(k)` por fila (descartado):** si la serie del pozo tiene un **hueco** (meses faltantes), `shift` toma la fila anterior *disponible*, que puede no ser t-k. El lag queda con horizonte equivocado y se contamina silenciosamente.
- **Merge por calendario (elegido):** se busca explícitamente el valor en `periodo - k meses` (`features._calendar_lag`, vía join sobre `(idpozo, periodo)`). Si ese mes no existe, queda `NaN` (honesto), no un valor de otro horizonte. Robusto a huecos, que en este dataset son frecuentes.

### 2. Qué features incluir

Se priorizaron familias con **fundamento físico/temporal** y de costo bajo, evitando redundancia y leakage:

- **Autoregresivas de `prod_pet`** (roll3, delta1, lag12, acum6): la variable más predictiva es la propia historia del pozo (corr 0,95). Cubren nivel, tendencia, estacionalidad y volumen.
- **`water_cut`:** alternativa a sumar `prod_agua` cruda; el ratio es un indicador de madurez interpretable y comparable entre pozos.
- **`produjo_mes_pasado`:** dado que ~25% de los targets son 0 (pozos parados), un flag de actividad separa el problema "¿produce?" del "¿cuánto?".
- **`prod_vecinos_mean`:** se evaluó (a) **nada** (ignorar el espacio), (b) **promedio por área/yacimiento** (categórica) y (c) **k-vecinos por coordenadas** (elegido). El promedio por área depende de la calidad/granularidad de la categórica; los k-vecinos por coordenadas con `NearestNeighbors` capturan proximidad real del reservorio y degradan con gracia. Las áreas categóricas igual entran por one-hot (ADR-032), así que ambas señales conviven.
- **Descartadas por ahora:** curva de declinación tipo Arps (ya considerada en ADR-029, requiere ajustar una tasa por pozo → mini-modelo, queda como mejora), e interacciones explícitas entre features (se delega a los modelos de árboles, que las capturan solos).

### 3. Features que aprenden parámetros (escalado, medias globales)

- **Aprender estadísticos en el cálculo de la feature (descartado):** p. ej. normalizar `prod_pet` por la media histórica de todo el panel metería datos de val/test en filas de train (leakage val→train).
- **Features sin parámetros aprendidos (elegido):** todas las de `ml/features.py` son funciones puras de la propia serie del pozo hasta t. El **escalado** (para la regresión lineal) **no** vive acá: se hace en el `Pipeline` del entrenamiento, ajustado solo en train / en el tramo de train de cada fold (ver ADR-034). Así feature engineering y preprocesamiento quedan separados y el leakage val→train se controla en un solo lugar.

### 4. Vecinos: definición del conjunto

- **Conjunto de vecinos por coordenadas estáticas (elegido):** el *quién* es vecino se fija con `coordenadax/coordenaday` (atributo del pozo, no del target). El *valor* promediado es `prod_pet(t)` de esos vecinos (mes t, ≤ t). No usa el target ni datos futuros.
- **Vecinos por similitud de producción (descartado):** definir vecindad con la propia producción mezcla la variable a predecir en la construcción del vecindario (riesgo de leakage y de fuga circular).

## Decisión

Adoptar las **7 features derivadas** de la tabla, implementadas como funciones modulares en `ml/features.py` y agregadas en `build_basic_dataset`:

- **Lags y ventanas por merge de calendario** (no `shift`), robustos a huecos.
- **Sin parámetros aprendidos** en las features (el escalado se delega al Pipeline del entrenamiento, ADR-034).
- **Vecinos espaciales** por k-vecinos de coordenadas, promediando producción del mes t.
- Los `NaN` de las features de historia (primeros meses de cada pozo) se **imputan en el Pipeline de entrenamiento, no acá** (las de volumen/lags con **0 + flag `*_isna`**; ver ADR-039 para el esquema por feature).

Cada feature queda documentada (origen Gold + cálculo) como **contrato del feature store**, donde se materializan para que entrenamiento e inferencia las calculen igual (evitar *training-serving skew*).

## Consecuencias

**Positivas:**
- Señal temporal y espacial **leak-free** en las dos direcciones críticas, auditada en el notebook `02_feature_engineering.ipynb` (p. ej. `prod_vecinos_mean` coincide con el promedio manual de los k-vecinos en el mes t y difiere del de t+1).
- Features **interpretables** y baratas de calcular; reproducibles (funciones puras).
- Tabla origen→cálculo lista como **contrato** para el feature store.

**Negativas / trade-offs:**
- Las features de historia (lag12, acum6, roll3) generan **`NaN` en los primeros meses** de cada pozo; se resuelven con imputación en el Pipeline, pero reducen la señal al inicio de la serie.
- `prod_vecinos_mean` asume que la **cercanía geográfica** implica reservorio compartido; no siempre es cierto (pozos cercanos en formaciones distintas).
- El conjunto de features es **acotado a propósito** (baseline de modelado); curvas de declinación, antigüedad explícita e interacciones quedan como mejoras futuras.
- Hay que mantener la **paridad de cálculo** con el feature store: si una feature se computa distinto en inferencia, aparece skew.

---

> Relacionados: **ADR-031** (construcción del dataset y anti-leakage temporal, que prometió este ADR), **ADR-032** (encoding de categóricas), **ADR-029** (baselines a superar) y **ADR-034** (algoritmo y validación/tuning, que consume estas features).
