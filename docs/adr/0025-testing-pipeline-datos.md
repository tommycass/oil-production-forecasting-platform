# Título: ADR-025: Estrategia de testing del pipeline de datos (extracción + DAGs)

**Estado:** Aceptado

## Contexto

ADR-009 fijó la estrategia de testing **de la API**, y explícitamente acotó su alcance a
esa superficie: *"cuando se reemplace por una fuente real de datos, la estrategia deberá
revisarse para incluir tests de integración contra esa fuente"*. La Fase 2 agregó esa
fuente real y un pipeline (`data_pipeline/`) con extracción, validación de schema y assets
de Dagster particionados — pero **sin un ADR que documente cómo se testea**. Este ADR cubre
ese hueco para la zona del Data Engineer (extracción + Bronze + orquestación).

El pipeline tiene **contratos de comportamiento que pueden romperse en silencio**, y son
justamente los que sostienen requisitos `DEBE` de la consigna:

- **Idempotencia:** re-correr no debe duplicar ni corromper (ADR-012/021).
- **Aislamiento de particiones:** reprocesar un mes (backfill) **no debe tocar** los demás
  meses (ADR-011/013) — es la base del reprocesamiento por fecha.
- **Fail-fast de schema:** si la fuente cambia las columnas, la ingesta debe abortar **sin
  escribir Bronze** (ADR-022).
- **Política de retries:** los assets deben tener retry con backoff exponencial (ADR-011).
- **Fidelidad del crudo:** Bronze guarda todo como texto y descarta el BOM (ADR-013).

Una regresión en cualquiera de estos no se manifiesta como un error visible para quien
edita el código, pero rompe el reprocesamiento, la idempotencia o la integridad de Bronze.

Restricciones de los tests:

- Deben correr en **CI sin red** (la descarga real de datos.gob.ar son ~144 MB y la fuente
  es intermitente) y **sin un Postgres real** (rápido, determinístico).
- Los modelos dbt (Silver/Gold) tienen **sus propios tests de datos** corridos por
  `dbt build` (ADR-016): son una capa de testing distinta y no son objeto de este ADR.

### Evaluación de alternativas

| Criterio | Sin tests de pipeline (solo dbt + corridas manuales) | E2E contra fuente real + Postgres real en CI | Solo unit de funciones puras (sin Dagster) | **Unit + integración de assets con I/O mockeado** |
|---|---|---|---|---|
| Corre en CI sin red ni DB | ✅ (no hay tests) | ❌ descarga 144 MB + levanta PG | ✅ | ✅ (monkeypatch red + tmp dirs) |
| Cubre idempotencia / aislamiento de particiones | ❌ | ✅ pero lento/frágil | ❌ (no ejercita Dagster) | ✅ (`materialize()` + assert de `mtime`) |
| Cubre retry policy y wiring de assets | ❌ | Parcial | ❌ | ✅ (inspecciona `retry_policy`, deps) |
| Cubre fail-fast de schema sin escribir Bronze | ❌ | ✅ | Parcial | ✅ (assert de que no se creó el landing) |
| Determinismo / independencia de la fuente | n/a | ❌ (depende de datos.gob.ar) | ✅ | ✅ (CSV fijo en memoria) |
| Velocidad | n/a | ❌ (minutos) | ✅ | ✅ (~4 s la suite) |

## Decisión

Adoptamos **pytest con unit tests + tests de integración de assets con I/O mockeado**
(alternativa 4), en `data_pipeline/tests/`, como **gate del job `test-pipeline` de CI**
(job separado de `test` que corre los tests de la API; ambos son `needs` de `build`,
de modo que `build` solo ocurre si los dos pasan). El patrón concreto:

- **Red mockeada:** se reemplaza `requests.get` (monkeypatch) por un **CSV fijo en memoria
  con BOM** que trae el schema completo de la fuente. Cero descargas reales.
- **Filesystem aislado:** `LANDING_DIR`/`BRONZE_DIR` se redirigen a `tmp_path`. No ensucia
  el repo ni depende de datos previos.
- **Ejecución real de assets:** se usa `dagster.materialize()` para correr los assets
  **in-process** (incluye particiones, deps y retry policy), no solo las funciones sueltas.
- **Asserts sobre los contratos**, no sobre el contenido del dato:
  - BOM descartado y todo-texto (fidelidad de Bronze).
  - **Aislamiento de particiones:** materializar enero no cambia el `mtime` de febrero.
  - **Idempotencia:** dos corridas dejan el mismo resultado, sin duplicar.
  - **Retry policy:** `max_retries=3`, `backoff=EXPONENTIAL`, `delay=5`.
  - **Fail-fast de schema:** una fuente con columnas faltantes lanza `SchemaContractError`
    y **no escribe el landing**.

### Regla de scope

Se testean **contratos de comportamiento del pipeline** (idempotencia, aislamiento de
particiones, retry, fail-fast, fidelidad del crudo), **no**:

- El **contenido de la fuente** (qué pozos/valores trae): es dato externo, cambia, y no es
  un contrato nuestro.
- La **lógica de los modelos dbt** (Silver/Gold): la cubren los **tests de datos de dbt**
  corridos en `dbt build` (ADR-016), que son otra capa.
- La **red real y un DW real**: se mockean a propósito (ver alternativas).

### Por qué I/O mockeado sobre las alternativas

- **Sobre no tener tests:** la idempotencia y el aislamiento de particiones son exactamente
  los invariantes en los que se apoyan los requisitos de idempotencia y backfill de la
  consigna; una regresión silenciosa ahí rompe el reprocesamiento sin que nadie lo note.
- **Sobre E2E real:** descargar 144 MB y levantar Postgres en cada corrida de CI es lento y
  frágil, y **no permite** testear de forma determinística el aislamiento de particiones ni
  el comportamiento ante un cambio de schema (haría falta controlar el input).
- **Sobre solo funciones puras:** se perdería el wiring de Dagster (particiones, retry
  policy, deps entre assets), que es **donde vive la correctitud del backfill**;
  `materialize()` lo ejercita barato, in-process.

## Consecuencias

**Positivas:**
- CI valida los contratos de reprocesamiento **sin red ni DB**, rápido y determinístico,
  desacoplado de la disponibilidad de datos.gob.ar.
- Simétrico con ADR-009: la API y el pipeline tienen, cada uno, su estrategia documentada y
  su propio job de CI (`test` y `test-pipeline`); ambos gatean `build`.
- Patrón replicable: agregar un asset o una regla nueva tiene costo marginal bajo de cubrir.

**Negativas:**
- Al mockear la red, los tests **no detectan en runtime** que la fuente cambió su schema;
  eso lo cubren la **validación en la ingesta** (ADR-022, que sí corre contra el CSV real) y
  `dbt source freshness`.
- La lógica SQL de Silver/Gold **no** la cubre pytest, sino los tests de datos de dbt
  (ADR-016): son dos capas de testing que hay que mantener en conjunto.
- Depende de la API de testing de Dagster (`materialize()`); un cambio mayor de Dagster
  podría requerir ajustar los tests (acotado a `data_pipeline/tests/`).

## Decisiones Técnicas Posteriores

- **Smoke de integración del DW (opcional):** un test que siembre Bronze de muestra
  (`seed_sample_bronze.py`) contra un Postgres local efímero y corra `dbt build`, para
  cubrir la capa dbt end-to-end fuera del unit test.
- **Contract test de la fuente:** versionar el header real del CSV y testear que
  `EXPECTED_COLUMNS` lo cubre, para detectar drift de la fuente antes de runtime.
- **Cobertura mínima:** definir si se exige un umbral de cobertura en el job de CI del
  pipeline (hoy es gate por pass/fail, sin umbral).
