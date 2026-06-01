# Título: ADR-014: Arquitectura de capas para el pipeline de datos (Medallion)

**Estado:** Aceptado

## Contexto

La Fase 2 exige procesar dos fuentes públicas de datos.gob.ar (producción mensual por pozo y catálogo de pozos por operadora) hasta un Data Warehouse en modelo estrella, cumpliendo varios requisitos no funcionales de la adenda que condicionan **cómo organizamos las capas de transformación**:

- El procesamiento DEBE ser **idempotente** y **reprocesable por fecha** (la fuente de producción corrige meses ya publicados; columna `rectificado`).
- DEBE existir un procedimiento **verificable de backfill / reprocesamiento histórico**.
- DEBE haber **chequeos de calidad persistidos** con consecuencia operativa (bloqueo de promoción aguas abajo).
- DEBE poder **seguirse el linaje** de los datos a nivel tabla.

La pregunta de este ADR no es *qué herramienta* (eso lo cubren ADR-011 orquestación y la elección de dbt para transformar), sino **en cuántas zonas lógicas dividimos el flujo y qué responsabilidad tiene cada una**. La decisión condiciona dónde persiste el crudo, dónde se limpia, dónde se modela para negocio y dónde se ubica el control de calidad.

Se evaluaron tres enfoques de capas.

### Evaluación de alternativas

| Criterio (derivado de la consigna) | ETL clásico (staging → DW) | Lakehouse 2 capas (raw → marts) | Medallion (Bronze → Silver → Gold) |
|---|---|---|---|
| Crudo persistido para backfill | No (staging efímero) | Sí (raw) | Sí (Bronze) |
| Reprocesar un mes sin re-descargar | No (hay que volver a la fuente) | Parcial (raw sin partición clara) | Sí (Bronze particionado por `anio/mes`) |
| Separación limpieza ↔ modelado de negocio | Mezcladas en el job de carga | Mezcladas en "marts" | Explícita (Silver limpia, Gold modela) |
| Punto natural para el gate de Data Quality | Difuso (pre-carga) | Difuso | Claro (entre Silver y Gold) |
| Linaje legible por capa | Bajo (un solo salto) | Medio | Alto (3 saltos nombrados) |
| Encaje con assets de Dagster (ADR-011) | Bajo | Medio | Alto (un asset por capa) |
| Complejidad / nº de materializaciones | Baja | Media | Media-alta |

**ETL clásico** transforma en vuelo y carga directo a las tablas finales: simple, pero no deja crudo persistido, así que un backfill obliga a volver a descargar de la fuente y el control de calidad queda difuso antes de la carga. No cumple bien idempotencia ni reprocesamiento por fecha sin reinventarlos. **Lakehouse de dos capas** sí retiene el crudo, pero al colapsar limpieza y modelado en una sola capa "curada" pierde el lugar natural donde insertar el gate de calidad y donde un analista distingue "dato limpio" de "dato listo para negocio". **Medallion** agrega exactamente la zona intermedia (Silver) que separa esas dos responsabilidades, a costa de una materialización más.

## Decisión

Adoptamos la **arquitectura Medallion de tres capas**:

- **Bronze** (dueño: Data Engineer, ADR-013): crudo tal cual viene de la fuente, en parquet versionado por fecha de ingesta y particionado por `anio/mes`. Inmutable y append/merge según ADR-012. Es la red de seguridad que habilita backfill sin re-descargar.
- **Silver** (dueño: Analytics Engineer): **una fila limpia y tipada por registro de origen**. Aplica casteo de tipos, deduplicación por clave de negocio (`idpozo + anio + mes` en producción; `idpozo` en el catálogo), normalización de nombres de pozos/operadoras y manejo de nulos. No mezcla lógica de negocio ni reglas dimensionales.
- **Gold** (dueño: Analytics Engineer): **modelo estrella** servido a BI y gobierno — `fact_produccion_mensual` + `dim_pozo`, `dim_operadora`, `dim_yacimiento`, `dim_fecha` con surrogate keys (ver ADR-015). Es la única capa que consumen Persona C (Metabase, DataHub) y la API.

El **gate de Data Quality se ubica entre Silver y Gold** (ADR-016): los checks corren sobre Silver y, si falla uno crítico, **bloquean la materialización de Gold**. Las tres capas se implementan como modelos dbt sobre PostgreSQL, alineados con los software-defined assets de Dagster (ADR-011), de modo que cada capa es un asset con dependencias declaradas.

### Por qué Medallion sobre las alternativas

- **El backfill es de primera clase.** Bronze retiene el crudo particionado por mes, así que reprocesar un mes corregido por la fuente re-materializa Silver/Gold desde Bronze sin volver a descargar. Con ETL clásico habría que re-extraer; el requisito de reprocesamiento por fecha quedaría a medias.
- **El gate de calidad tiene un lugar natural.** Separar Silver (limpio) de Gold (negocio) crea la frontera exacta donde la consigna pide "bloqueo de promoción aguas abajo". En un esquema de dos capas ese punto no existe sin inventarlo.
- **El linaje es legible.** Tres zonas nombradas (Bronze→Silver→Gold) se leen directo en el grafo de assets de Dagster y en DataHub, cumpliendo el requisito de linaje a nivel tabla con nombres que comunican intención.

## Consecuencias

**Positivas:**
- Backfill e idempotencia sostenidos por Bronze inmutable + materializaciones determinísticas aguas abajo.
- Frontera clara para el control de calidad (Silver→Gold) y para asignar dueños por capa.
- Linaje autoexplicativo por capa, reutilizable por gobierno (DataHub) y por la documentación del modelo.

**Negativas:**
- Una materialización extra (Silver) frente al enfoque de dos capas: más objetos en el DW y más tiempo de cómputo por corrida.
- Riesgo de "Silver que solo copia Bronze" si no se es disciplinado: se mitiga exigiendo que toda limpieza/normalización viva en Silver y toda regla de negocio en Gold.

## Decisiones Técnicas Posteriores

- **Materialización dbt por capa:** Silver y Gold como `table` (no `view`) para que BI y gobierno lean datos materializados estables; reevaluable a `incremental` en Silver de producción si el volumen lo exige.
- **Esquemas en Postgres:** un esquema por capa (`silver`, `gold`) más el esquema de resultados de DQ (ver ADR-016), para que el linaje y los permisos se lean por zona.
- **Coordinación con A:** Silver consume Bronze parquet vía dbt (lectura de parquet a Postgres en la ingesta inicial o `read_parquet` según defina la integración Dagster↔dbt). Contrato de entrada ya acordado: grano y claves de ambas fuentes.
