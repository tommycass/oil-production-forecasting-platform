# Título: ADR-022: Validación del contrato de schema en la ingesta (fail-fast)

**Estado:** Aceptado

## Contexto

La extracción descarga por HTTP los CSV de datos.gob.ar y los persiste en Bronze **sin
validar su estructura**: se leen como texto y se escriben tal cual. Si la fuente —que no
controlamos— cambia su schema (renombra, elimina o reordena columnas), el dato roto entra
igual a Bronze, se carga a Postgres, y el problema **recién se detecta en Silver** (dbt),
con un error críptico de cast varios pasos después.

El gate de Data Quality de **ADR-016** ya cubre la dimensión *schema*, pero **en la capa
Silver**: corre después de escribir Bronze y cargarlo al DW. Eso protege a Gold (un check
`error` bloquea la promoción), pero deja tres problemas:

1. Te enterás tarde y lejos del origen (en dbt, no en la descarga).
2. Ya pisaste tu Bronze bueno con la estructura rota y desperdiciaste descarga + carga.
3. El error no dice "la fuente cambió la columna X".

La descarga HTTP es el **límite de confianza** con un sistema externo: es el lugar natural
para validar el contrato de columnas.

## Decisión

Se valida el **contrato de schema en la ingesta**, apenas se lee el CSV y **antes** de
escribir Bronze (`data_pipeline/extraction/validation.py`, llamado desde
`extract_pozos` y `descargar_landing`):

- Se define por fuente el conjunto de **columnas esperadas** en `config.EXPECTED_COLUMNS`
  (lo que consumen los modelos Silver; `fecha_ingesta` no está porque se agrega al cargar).
- **Falta alguna columna esperada → se lanza `SchemaContractError` y se aborta la
  extracción** (fail-fast). El asset de Dagster falla → los assets aguas abajo no corren →
  Bronze viejo queda intacto → se dispara la alerta (mismo stack que ADR-016).
- **Aparecen columnas de más → solo se avisa por log** (cambio aditivo, no corta).

Esto **complementa**, no reemplaza, el check de schema de Silver (ADR-016): la ingesta es
la primera red (estructura, en el origen) y Silver es la segunda (tipos y contenido, antes
de Gold).

### Estricto sobre tolerante

Se exige que estén **todas** las columnas esperadas (no solo un subconjunto crítico):
cualquier cambio estructural de la fuente es algo que el DE quiere mirar sí o sí, y un
único criterio ("están todas o corta") es más simple de razonar y mantener que una lista
de "críticas vs opcionales".

### Por qué en la ingesta y no solo en Silver

- **Fail-fast en el origen:** error claro ("faltan columnas X") en la descarga, no un cast
  roto tres pasos después.
- **Protege Bronze:** al cortar antes de escribir, no se pisa el Bronze bueno con una
  estructura rota; no se desperdicia carga ni cómputo.

### Compatibilidad con "Bronze fiel al crudo" (ADR-013)

El gate **no transforma** el dato: solo se niega a ingerir una estructura que rompe el
contrato. Bronze sigue guardando fielmente lo que **sí** ingiere. Es validar la
*estructura*, no el *contenido*.

## Consecuencias

**Positivas:**
- Un cambio de columnas de la fuente se detecta **en la puerta**, con mensaje claro, y
  frena el DAG antes de contaminar Bronze/DW.
- Bronze y Gold previos quedan intactos ante un schema roto; no hay trabajo desperdiciado.
- El contrato de columnas queda **explícito y versionado** en `config.EXPECTED_COLUMNS`.

**Negativas:**
- Hay que **mantener** la lista de columnas esperadas si la fuente evoluciona de forma
  legítima (una columna nueva requerida implica actualizar el contrato).
- Si la fuente publica columnas que no usamos, generan un **aviso** en cada corrida hasta
  que se agreguen al contrato (ruido informativo, no bloqueante).
- Acopla `config.py` (Data Engineer) al conjunto de columnas que consume Silver
  (Analytics Engineer): un cambio de necesidades de Silver puede requerir tocar el contrato.

## Decisiones Técnicas Posteriores

- **No reintentar ante schema roto:** hoy el `RetryPolicy` del asset reintenta 3 veces un
  `SchemaContractError` (inútil: el schema no se arregla solo). Evaluar distinguir el error
  de schema de los transitorios de red para no reintentar.
- **Severidad de columnas extra:** hoy es solo aviso; si se quisiera, podría elevarse a
  `warn` formal en el grafo/DQ.
- **Validación de tipos/dominios:** queda en Silver (ADR-016); este ADR cubre solo la
  *presencia* de columnas, no su contenido.
