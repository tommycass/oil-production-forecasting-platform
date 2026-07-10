# Título: ADR-032: Encoding de variables categóricas (one-hot en train con fallback explícito)

**Estado:** Propuesta

## Contexto

Las 22 features candidatas (ADR-031 §4) incluyen **14 variables categóricas** (`tipoextraccion`, `tipoestado`, `tipopozo`, `empresa`, `formprod`, `formacion`, `areapermisoconcesion`, `areayacimiento`, `cuenca`, `provincia`, `proyecto`, `clasificacion`, `subclasificacion`, `sub_tipo_recurso`). Los modelos tabulares no consumen strings, así que hay que **codificarlas a números**, y hacerlo (a) **sin leakage** y (b) de forma **robusta a categorías nuevas**: la cuenca incorpora operadoras y áreas con el tiempo, así que en val/test aparecen valores que no estaban en train.

La implementación es reutilizable en `ml/dataset.py` (`fit_onehot_encoder`, `transform_onehot`, `onehot_encode_dataset`).

### Evidencia

- Las 14 categóricas generan **365 columnas** one-hot (incluye una columna `DESCONOCIDO` por feature); el dataset pasa de 28 a 379 columnas.
- El grueso lo aportan las de **alta cardinalidad**: `areayacimiento` (119), `areapermisoconcesion` (93), `empresa` (41).
- En **test, el 10,4%** de las filas tiene una `empresa` que **no existe en train** (13 operadoras nuevas) — es un cambio de distribución real, no un error.

## Decisión

### 1. One-hot encoding como codificación base

Una columna 0/1 por categoría. Es simple, transparente y sirve para cualquier familia de modelos (lineales, árboles, boosting). Para alta cardinalidad genera muchas columnas ralas, asumido como costo del baseline (ver §5).

**Alternativas:** ordinal/label encoding (introduce orden falso), target/frequency encoding (mejor para alta cardinalidad pero más complejo y con su propio riesgo de leakage) — se posponen (§5).

### 2. El encoder se ajusta **solo con train** (anti-leakage)

`fit_onehot_encoder` aprende las categorías **únicamente de las filas `split == "train"`**. Las columnas resultantes son las categorías vistas en train (one-hot **completo sobre train**).

- **Motivo:** dejar que val/test definan columnas metería en el esquema de features información de qué categorías existen en el futuro (leakage). Una categoría que solo aparece en val/test **no** crea su columna.
- **Variante por fold para el tuning:** en la cross-validation (ADR-034) no alcanza con ajustar el encoder sobre todo train; el vocabulario debe aprenderse **con el train de cada fold**. Para eso, la misma lógica (one-hot + `DESCONOCIDO`) se encapsula en el transformer `OneHotDESC` (`ml/preprocessing.py`), que va dentro del `Pipeline`/`ColumnTransformer` y se reajusta por fold. `fit_onehot_encoder` (que filtra `split=="train"`) sigue sirviendo para el ajuste único train-vs-resto y para inferencia.

### 3. Fallback explícito `DESCONOCIDO` para nulls y categorías no vistas

Cada feature lleva una categoría explícita **`DESCONOCIDO`** (`ml.dataset.CATEGORICAL_NA_FILL`). En `transform`, todo valor nulo o **no visto en train** se mapea a `<feature>_DESCONOCIDO = 1` (vía `handle_unknown="ignore"` + mapeo explícito al sentinel).

- **Motivo:** una categoría nueva no debe romper el modelo ni quedar en un todo-cero implícito. Con el fallback, el "desconocido" queda **marcado de forma explícita e inspeccionable** y el bloque one-hot de la feature sigue sumando 1.
- En features que ya tienen nulls en train (p. ej. `tipoestado`), esa columna es **aprendible** (el modelo ve ejemplos de "desconocido" en train); en las que no, queda en 0 sobre train y solo se activa con desconocidos posteriores.
- Verificado: las 13 empresas que aparecen solo en val/test caen todas en `empresa_DESCONOCIDO = 1`.

### 4. No precomputar el vocabulario completo; usar `DESCONOCIDO` + reentrenamiento

Se evaluó "precomputar" todas las empresas/áreas posibles (desde el dato completo o un padrón externo) para que las nuevas ya tengan columna. **Se descarta:**

- Precomputar desde el dato completo (train+val+test) es **leakage**.
- Precomputar desde un padrón externo no es leakage, pero genera **columnas muertas**: una categoría con 0 filas en train no tiene coeficiente aprendible (lineal → 0; árbol → nunca splitea). No mejora la predicción y solo agrega memoria y fragilidad de esquema (nunca se conocen todas las categorías futuras).

**Decisión:** vocabulario fijo de train + fallback `DESCONOCIDO`. Toda categoría nula o no vista en train cae en `<feature>_DESCONOCIDO`, manteniendo el **esquema estable** para servir el modelo sin importar qué categorías nuevas aparezcan en inferencia. (Que una categoría nueva pase a tener columna propia y aprendible requeriría ampliar la ventana de train; hoy el split es fijo, ADR-028/040.)

### 5. No persistir la matriz codificada; target/frequency encoding como mejora futura

- La matriz one-hot (365 columnas ralas) **no se guarda a CSV**: se reconstruye al vuelo con `onehot_encode_dataset(ds)` (o reusando el `encoder` con `transform_onehot`). El CSV persistido es el dataset básico con las categóricas crudas (ADR-031).
- Para las categóricas de **alta cardinalidad** (`empresa`, áreas), si el modelado muestra que aportan, se evaluará **target/frequency encoding** (mapea cada categoría a un número, con default sensato para nuevas, sin explosión de columnas). Queda como trabajo futuro; el one-hot es la base.

## Consecuencias

**Positivas:**
- Codificación **leak-free** (ajustada en train) y **robusta a categorías nuevas** sin romper el modelo.
- El fallback `DESCONOCIDO` es **explícito e inspeccionable** y, donde hay nulls en train, aprendible.
- Esquema de features **estable entre reentrenos**, apto para servir.
- Funciones **reutilizables** (`fit`/`transform`/`encode`) que separan fit (train) de transform (cualquier conjunto).

**Negativas / trade-offs:**
- Alta cardinalidad → **muchas columnas ralas** (365), costoso en memoria para modelos lineales; mitigado con `uint8` y, a futuro, target encoding.
- Las categorías nuevas **pierden su señal específica** (caen en `DESCONOCIDO`) mientras el vocabulario de train no las incluya. Aceptable porque `empresa`/áreas son predictores débiles frente a los lags del propio pozo.
- Depender del reentrenamiento implica definir su **cadencia** operativa (encaja con el monitoring de Fase 1).

---

> Relacionados: **ADR-031** (construcción del dataset y selección de columnas), **ADR-028** (encuadre del problema).
