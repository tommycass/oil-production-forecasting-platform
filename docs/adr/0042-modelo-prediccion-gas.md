# Título: ADR-042: Segundo modelo predictivo — forecast de gas (`prod_gas`) reutilizando el pipeline de petróleo

**Estado:** Propuesta

## Contexto

El ADR-028 encuadró el problema predictivo de la Fase 3 y, en su decisión de **alcance**, fijó como único target la **producción de petróleo** (`prod_pet` del mes t+1), dejando el gas **explícitamente afuera**:

> *"Restringir al universo petrolero deja afuera el gas (decisión de alcance); si el equipo quisiera pronosticar gas, habría que revisar este ADR."* (ADR-028, Consecuencias)

Los pozos producen **petróleo y gas a la vez**, así que el target no era obvio. El equipo consultó la duda a la cátedra (Discord, 26–29/06/2026):

> **mmichanie:** *"El target tiene que ser la producción de petróleo o la de gas? Lo elegimos nosotros o esperan que hagamos dos modelos (uno para cada uno)?"*
> **Mati Grinberg (cátedra):** *"Ambas sería la idea."*

Con esa confirmación, hay que pronosticar **ambas** producciones. Este ADR **extiende el alcance del ADR-028** (sus secciones 2 *Target y grano* y 3 *Universo de entrenamiento*) para incorporar un **segundo modelo** cuyo target es el gas, decidiendo cómo se relaciona con el modelo de petróleo ya existente (`ml/`, ADR-028 a ADR-041) y, sobre todo, garantizando que el reuso **no introduzca leakage**.

## Decisión

### 1. Dos modelos independientes, un target cada uno (no multi-salida)

**Alternativas consideradas:**
- **Un único modelo multi-salida** (predecir `prod_pet` y `prod_gas` a la vez): comparte representación, pero **mezcla dos targets de escala y comportamiento distintos**, complica métricas/baseline/promoción y obliga a un único criterio de campeón para dos fenómenos.
- **Dos modelos independientes (elegida):** un modelo por target, cada uno con su baseline, su métrica, su campeón y su versión en el registry.

**Decisión:** **dos modelos separados** que comparten el **mismo encuadre** del ADR-028 (regresión tabular supervisada **global**, horizonte **t+1**, grano **(pozo, mes)**). El de petróleo ya existe; este ADR agrega el de gas. Cada modelo se entrena, evalúa, versiona y promueve de forma **independiente**.

### 2. Target y universo del modelo de gas

- **Target:** `prod_gas` (m³ de gas) del **mes siguiente (t+1)**, mismo grano (pozo, mes) que petróleo.
- **Universo gasífero (análogo al petrolero del ADR-028 §3):** pozos con **al menos un mes de `prod_gas > 0`**, definido **solo con `train`** (`periodo <= TRAIN_END`) para no usar datos de val/test en la selección. Excluye pozos que nunca producen gas (inyección/sumidero/petroleros puros), evitando ceros estructurales ajenos al fenómeno. Es el mismo criterio "por producción observada" que el ADR-028 prefiere sobre la etiqueta `tipopozo`.

> El EDA (ADR-028) ya señalaba que el gas está **más poblado** que el petróleo (`prod_gas` en 79% de los meses vs 64%), así que el universo gasífero es al menos tan amplio como el petrolero.

### 3. Reuso del pipeline parametrizando el target

**Decisión:** **no se duplica código**; se **parametriza el target** del pipeline de `ml/` (hoy `prod_pet` hardcodeado en `config.py`, `dataset.py`, `features.py`, `modeling.py`, `baseline.py`). Con el target como parámetro, el modelo de gas reutiliza **tal cual**:

- **Mismas familias de modelos** (ADR-034): Ridge, Random Forest, XGBoost. Son regresores agnósticos al target.
- **Mismo baseline / vara de éxito** (ADR-029): persistencia `ŷ(t+1) = prod_gas(t)`, media móvil 3m y naïve estacional, ahora sobre gas.
- **Mismo split temporal de 3 vías y misma CV temporal** (ADR-028/034): los cortes `TRAIN_END`/`VAL_END` no dependen del target.
- **Mismo preprocesamiento** (ADR-039) y **mismo one-hot con `DESCONOCIDO`** (ADR-032).
- **Mismo tracking en MLflow** (ADR-030/037), con **experimento propio** del gas para no mezclar runs, y su **propio modelo en el registry**.

### 4. Features: crudas compartidas + ingeniería en versión gas

- **Crudas compartidas** (sin cambios): `prod_pet`, `prod_gas`, `prod_agua`, `tef`, `profundidad`, `coordenadax/y`, `mes` y las 14 categóricas. Mantener `prod_pet(t)` como feature del modelo de gas es **válido** (es una medida del mes t, no del futuro) y aporta señal cruzada (petróleo y gas asociados).
- **Ingeniería en versión gas:** las features de ingeniería de `ml/features.py` están hoy construidas sobre `prod_pet` (`prod_pet_roll3`, `prod_pet_delta1`, `prod_pet_lag12`, `prod_pet_acum6`, vecinos, etc.). Para el modelo de gas se **espejan sobre `prod_gas`** (`prod_gas_roll3`, …), que es la señal autorregresiva y estacional propia del target. Se construyen con el **mismo mecanismo de lag por calendario** (ADR-033), así que heredan su garantía anti-leakage. `water_cut` (agua/(agua+petróleo)) y `produjo_mes_pasado` se mantienen o adaptan según aporten al gas; se decide en el notebook de modelado.

### 5. Confirmación anti-leakage (independiente del target)

La protección anti-leakage del pipeline es **temporal**, no depende de qué variable sea el target. Para gas se mantienen los cuatro mecanismos:

| Mecanismo | Por qué vale igual para gas |
|---|---|
| **Futuro→pasado** | Features en `t` (lags por calendario, no `shift` de filas), target en `t+1`. `prod_gas(t)` como feature y `prod_gas(t+1)` como target son **meses distintos** → no es leakage. |
| **Val→train (estadísticos)** | Imputación, one-hot y escalado viven en el `Pipeline`, reajustados por fold (ADR-034/039). |
| **Split y CV temporal** | train = pasado, val/test = futuro; CV de ventana expansiva. Cortes independientes del target. |
| **Universo solo-train** | El universo gasífero se define con `prod_gas > 0` y `periodo <= TRAIN_END`. |

## Consecuencias

**Positivas:**
- Cumple lo pedido por la cátedra (**ambos targets**) sin reescribir el pipeline: el de gas **reusa** dataset, preprocesamiento, modelos, baseline, split, tracking y orquestación.
- Parametrizar el target **deja el pipeline más general** y elimina el `prod_pet` hardcodeado, reduciendo el riesgo de inconsistencias.
- Métricas, baseline y promoción **independientes** por target: cada modelo se juzga contra su propia vara de éxito.

**Negativas / trade-offs:**
- Hay que **mantener dos modelos** (dos campeones, dos versiones en el registry, dos experimentos MLflow) y, en la API, exponer **ambos** o un parámetro de target en `/predict` (a definir; revisar ADR-035).
- La **orquestación del retrain** (ADR-041) debería reentrenar ambos modelos; queda pendiente extender el job.
- `water_cut` está definido sobre petróleo y agua; su utilidad como feature del modelo de gas es **menor** y se evalúa en el notebook.
- Este ADR **enmienda el alcance** del ADR-028 (§2 y §3): conviene anotarlo allí para que la decisión "solo petróleo" no se lea como vigente.

---

> Relacionados: **ADR-028** (encuadre del problema y alcance que este ADR extiende), **ADR-029** (baseline / vara de éxito, reusado para gas), **ADR-031/033** (dataset y feature engineering que se parametrizan), **ADR-032/039** (encoding y preprocesamiento, sin cambios), **ADR-034** (algoritmo y CV temporal, reusados), **ADR-030/037/040** (tracking, servidor y promoción en MLflow, un modelo por target), **ADR-035** (contrato de `/predict`, a revisar para exponer el target de gas) y **ADR-041** (orquestación del retrain, a extender a ambos modelos).
