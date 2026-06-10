# Título: ADR-016: Estrategia de Data Quality

**Estado:** Aceptado

## Contexto

La adenda impone requisitos concretos de calidad de datos, no opcionales:

- Chequeos con **mínimo 3 dimensiones de calidad** (de: schema, completeness, validity, uniqueness, freshness; e idealmente linaje).
- Resultados **persistidos** (no solo asserts en runtime que se pierden al terminar el proceso).
- Fallar un check **DEBE tener consecuencia operativa**: alerta, **bloqueo de promoción aguas abajo**, o marca de calidad visible.

En ADR-014 fijamos que el gate de calidad vive **entre Silver y Gold**, y en el stack ya elegimos **dbt sobre PostgreSQL** para las transformaciones. Este ADR decide *con qué framework* implementamos y persistimos la calidad, y cómo se materializa la consecuencia operativa.

Las fuentes tienen suciedad típica de datos.gob.ar que motiva los checks: nulos en medidas, posibles duplicados por la corrección de meses (`rectificado`), nombres de operadora con variantes, valores fuera de rango (producción negativa) y la necesidad de saber qué tan fresca está la última ingesta.

### Evaluación de alternativas

| Criterio (derivado de la consigna) | dbt tests + dbt-expectations | Great Expectations | Soda Core |
|---|---|---|---|
| Integración con el stack (dbt/Postgres ya elegidos) | Nativa (misma herramienta) | Externa (orquestar aparte) | Externa (YAML + CLI aparte) |
| Persistencia de resultados | Sí (`store_failures` → tablas en Postgres) | Sí (Data Docs / store) | Sí (Soda Cloud o tabla) |
| Bloqueo de promoción aguas abajo | Nativo (un test `error` corta `dbt build` antes de Gold) | Manual (checkpoint + lógica en el orquestador) | Manual (exit code + lógica en el orquestador) |
| Cobertura de dimensiones de calidad | Alta con dbt-expectations (schema, completeness, validity, uniqueness, freshness) | Muy alta (suite extensa) | Alta |
| Linaje en el mismo grafo | Sí (tests son nodos dbt, visibles en docs y DataHub) | No (herramienta aparte) | No |
| Peso operativo (servicios/deps extra) | Mínimo (ya está dbt) | Alto (otra herramienta + store + docs) | Medio |
| Curva de aprendizaje para el equipo | Baja (ya usamos dbt) | Media-alta | Media |

**Great Expectations** ofrece la suite de expectativas más rica y unos Data Docs visuales muy buenos, pero es una herramienta separada: hay que orquestar checkpoints, mantener su store y su contexto, y el bloqueo de promoción se programa a mano en el orquestador. Para nuestro alcance, ese peso no compensa. **Soda Core** es más liviano y declarativo (checks en YAML), pero sigue siendo una pieza externa a dbt y el linaje de sus checks no aparece en el grafo de transformación. **dbt tests + dbt-expectations** corren dentro del mismo `dbt build` que materializa las capas: los tests son nodos del DAG de dbt, sus fallos pueden persistirse en tablas y, al ser dependencias, **cortan la construcción de Gold automáticamente** cuando algo crítico falla.

## Decisión

Implementamos Data Quality con **dbt tests, extendidos con el paquete `dbt-expectations`**, ejecutados sobre los modelos **Silver** como gate previo a **Gold**.

### Dimensiones de calidad cubiertas (≥3, cubrimos 5)

| Dimensión | Cómo se chequea (ejemplos) | Sobre |
|---|---|---|
| **Schema** | tipos y columnas esperadas (`dbt_expectations.expect_column_values_to_be_of_type`, contracts de dbt) | Silver producción y pozos |
| **Uniqueness** | unicidad de la clave de negocio (`unique` / `dbt_utils.unique_combination_of_columns` sobre `idpozo+anio+mes`) | Silver producción |
| **Completeness** | no-nulos en claves y medidas críticas (`not_null` en `idpozo`, `anio`, `mes`) | Silver ambas |
| **Validity** | rangos y dominios (`expect_column_values_to_be_between` para producción ≥ 0; `anio`/`mes` en rango; `accepted_values` para tipo de recurso) | Silver producción |
| **Freshness** | antigüedad de la última ingesta (`dbt source freshness` / check sobre `max(fecha_data)`) | Silver / source Bronze |

> **Schema en dos capas (ADR-022):** además de este check en Silver, el schema se valida
> **en la ingesta** (fail-fast): si la fuente cambia las columnas, la extracción aborta
> antes de escribir Bronze. La ingesta es la primera red (presencia de columnas, en el
> origen); Silver es la segunda (tipos y contenido, antes de Gold).

### Persistencia de resultados

- `store_failures: true` en la config de tests → cada test fallido **persiste sus filas ofensoras** en tablas del esquema `dq` de Postgres (`dq.<nombre_test>`). No son asserts efímeros: quedan consultables después de la corrida.
- Además materializamos un modelo **`dq.dq_results`** que consolida, por corrida, cada check con: nombre, dimensión de calidad, severidad, estado (pass/fail), nº de filas que fallaron y timestamp. Es la "marca de calidad visible" que Persona C puede exponer en Metabase y que documenta el historial de calidad.

### Consecuencia operativa (las tres, en capas)

1. **Bloqueo de promoción Silver→Gold (principal):** los checks críticos se marcan con `severity: error`. Como Gold depende de Silver en el DAG de dbt, un `error` **aborta `dbt build` antes de materializar Gold** — la promoción aguas abajo queda bloqueada por construcción. Los checks no críticos van como `severity: warn` (registran pero no bloquean).
2. **Alerta:** Dagster detecta el fallo del step de dbt y dispara una alerta reutilizando el **stack de Alertmanager/Slack ya montado en Fase 1** (webhook existente), notificando qué check crítico falló.
3. **Marca de calidad visible:** `dq.dq_results` + las tablas de `store_failures` dejan el resultado navegable en el DW y en DataHub.

### Manejo de filas inválidas: cuarentena vs bloqueo

No todo fallo de validez debe frenar el pipeline. Distinguimos dos casos:

- **Invariantes de integridad** (unicidad de `idpozo+anio+mes`, no-nulos de claves, integridad referencial fact→dim): si fallan, el problema es *nuestra* lógica, no la fuente → `severity: error`, **bloquean** la promoción a Gold.
- **Suciedad física irreparable de la fuente** (producción/inyección negativa: físicamente imposible y NO marcada como `rectificado`): la fuente pública la trae así y A no puede corregirla (Bronze es crudo inmutable). Bloquear para siempre dejaría el DW sin actualizarse. Para estos casos aplicamos el **patrón de cuarentena (quarantine/dead-letter)**: las filas inválidas se **desvían** del Silver limpio a la tabla `dq.silver_produccion_rechazos` con su `motivo_rechazo`, en vez de dropearlas en silencio. Así: (a) Silver queda limpio y Gold suma bien; (b) las filas excluidas quedan auditables y se reconcilia `bronze = silver + rechazos`; (c) el check `expect_column_values_to_be_between ≥ 0` sobre Silver pasa a ser un **invariante post-limpieza** (si alguna vez fallara, significaría que la cuarentena se rompió → ahí sí bloquea, legítimamente).

> La **decisión formal con comparación de alternativas** (bloqueo duro vs clamp a 0 vs exclusión silenciosa vs `warn` vs cuarentena) está en el **[ADR-019](0019-tratamiento-registros-invalidos.md)**.

## Consecuencias

**Positivas:**
- Cero herramientas nuevas: la calidad vive en el mismo `dbt build` y en el mismo grafo de linaje que las transformaciones.
- Bloqueo de promoción **garantizado por las dependencias del DAG**, no por código pegamento.
- Resultados persistidos y consultables (tablas `dq.*`), reutilizables por BI y gobierno.
- Cubre 5 de las 6 dimensiones sugeridas, superando el mínimo de 3.

**Negativas:**
- Menos expectativas "exóticas" que Great Expectations y sin Data Docs visuales propios (lo suplimos con el modelo `dq.dq_results` + dashboard en Metabase).
- `store_failures` agrega tablas al DW que hay que versionar/limpiar (retención a definir).
- La severidad de cada check (error vs warn) es una decisión de criterio que hay que mantener explícita y revisada.

## Decisiones Técnicas Posteriores

- **Clasificación de severidad:** definir qué checks son `error` (bloquean: unicidad de PK, no-nulos de claves, integridad referencial) vs `warn` (variantes de nombres, outliers leves). La producción negativa NO bloquea: se desvía a cuarentena en Silver (ver "Manejo de filas inválidas") y el check ≥ 0 queda como invariante post-limpieza. Se documenta junto a cada test.
- **Umbral de freshness:** acordar con A el SLA de frescura (p. ej. fallar si la última ingesta supera N días) según la cadencia real del DAG.
- **Retención de `store_failures`:** truncado/rotación de las tablas `dq.*` para que no crezcan sin límite.
- **Exposición en gobierno:** coordinar con C para que `dq.dq_results` se ingiera en DataHub como señal de calidad a nivel tabla.
