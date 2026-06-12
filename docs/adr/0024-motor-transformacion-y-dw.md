# Título: ADR-024: Motor de transformación (dbt) y del Data Warehouse (PostgreSQL/RDS)

**Estado:** Aceptado

## Contexto

ADR-011 eligió el **orquestador** (Dagster) y ADR-014 fijó la **arquitectura de capas**
(Medallion), pero ninguno decidió explícitamente, con alternativas, **dos piezas
fundacionales** sobre las que se apoya todo el resto: *con qué herramienta transformamos*
Bronze→Silver→Gold y *en qué motor* vive el Data Warehouse. Varios ADRs posteriores las dan
por "ya elegidas" (ADR-014 y ADR-016 mencionan "dbt sobre PostgreSQL" sin justificarlo), de
modo que esta decisión quedó implícita. Este ADR la documenta de forma retroactiva porque
condiciona ADR-016 (calidad), ADR-017 (gobierno/linaje) y ADR-018 (integración al grafo).

Hechos que condicionan la decisión:

- **El trabajo de transformación es SQL de conjuntos:** castear tipos, deduplicar por clave,
  normalizar nombres, generar un modelo estrella con surrogate keys. No hay ML ni lógica
  imperativa fila-a-fila; es set-based.
- **Volumen moderado:** ~406 mil filas de producción y ~84k de catálogo (ADR-019). No
  justifica un motor MPP ni cómputo distribuido.
- **Equipo de 3 en 2 semanas, infra ajustada:** EC2 chicas (2–4 GB) para API/monitoreo y
  una instancia dedicada de gobierno; se prefiere lo *managed* y de bajo overhead.
- **Múltiples consumidores del mismo DW:** BI (Metabase), gobierno (DataHub), la **API**
  (que ya lee `gold.*` vía `api/app/core/database.py`) y el propio pipeline. Necesitan un
  **servidor compartido** con SQL estándar y concurrencia, no un archivo local.
- **Requisitos que la herramienta de transformación debe habilitar:** gate de calidad
  persistido (ADR-016), linaje a nivel tabla/columna para gobierno (ADR-017) e idempotencia
  por full-refresh determinístico (ADR-021).

Son dos decisiones acopladas (la herramienta de transformación y el motor del DW deben
encajar), así que se deciden juntas.

### Evaluación de alternativas — herramienta de transformación

| Criterio | dbt-core | SQLMesh | Dataform | Pandas / scripts Python | Apache Spark |
|---|---|---|---|---|---|
| Transformación SQL set-based | Sí (SQL + Jinja) | Sí | Sí | No (imperativo) | Sí (pero distribuido) |
| Tests de calidad integrados | Sí (`dbt-expectations`, ADR-016) | Sí | Parcial | No (a mano) | No (a mano) |
| Artefacto de linaje (manifest/catalog) p/ DataHub | Sí, de primera clase | Parcial | Sí (BigQuery-céntrico) | No | No |
| Integración nativa con Dagster | Sí (`dagster-dbt`, ADR-018) | Incipiente | No | N/A (código propio) | Vía plugin |
| Encaje con Postgres | Nativo (`dbt-postgres`) | Sí | Débil (orientado a BigQuery) | Nativo | Requiere conector |
| Overhead / dependencias | Bajo (una lib) | Bajo | Medio (atado a GCP) | Bajo | Alto (cluster/JVM) |
| Madurez / comunidad / tutoriales | Alta | Media-baja | Media | Alta (pero no es ETL) | Alta |
| Curva para el equipo | Media (SQL+Jinja) | Media | Media | Baja | Alta |

### Evaluación de alternativas — motor del Data Warehouse

| Criterio | PostgreSQL 16 (RDS) | DuckDB | BigQuery / Snowflake / Redshift | SQLite |
|---|---|---|---|---|
| Servidor compartido (BI+API+gobierno concurrentes) | Sí | No (embebido, 1 proceso) | Sí | No (archivo) |
| Costo a esta escala (~406k filas) | Bajo (RDS chico) | Casi nulo | Alto (MPP + egress) | Nulo |
| Managed (backups, parches) | Sí (RDS) | No | Sí | No |
| SQL estándar para dbt + Metabase + DataHub + API | Sí | Sí (con matices) | Sí | Limitado |
| Encaje en la VPC / sin abrir egress | Sí (RDS en VPC) | N/A | Servicio externo | N/A |
| Adapter dbt de primera clase | Sí | Sí | Sí | Parcial |
| Persistencia entre corridas | Sí | Archivo a gestionar | Sí | Archivo |

## Decisión

Usamos **dbt-core (adapter `dbt-postgres`)** como herramienta de transformación y
**PostgreSQL 16 sobre Amazon RDS** como motor del Data Warehouse. Silver, Gold y los
resultados de Data Quality se materializan como modelos dbt en los esquemas `silver`,
`gold` y `dq` de una base Postgres por entorno (`oil_dw_staging`, `oil_dw_prod`),
env-driven por `POSTGRES_*` (`transform/profiles.yml`).

### Por qué dbt sobre las alternativas

- **Sobre Pandas/scripts Python:** la transformación es SQL de conjuntos; hacerla
  imperativa en Pandas obliga a escribir a mano los tests, el manejo de dependencias entre
  capas y, sobre todo, **no produce un artefacto de linaje**. dbt da los tests
  (`dbt-expectations`, ADR-016), el orden de materialización por dependencias, y el
  `manifest.json`/`catalog.json` que **DataHub consume directo** (ADR-017) — todo gratis.
- **Sobre SQLMesh/Dataform:** dbt es el más maduro y el que tiene **integración de primera
  clase con Dagster** (`dagster-dbt`, ADR-018) y con Postgres (`dbt-postgres`). Dataform es
  BigQuery-céntrico (no encaja con Postgres) y SQLMesh, aunque prometedor, aporta features
  (entornos virtuales, diffing de columnas) que no necesitamos a esta escala, con menos
  ecosistema y tutoriales para un equipo aprendiendo.
- **Sobre Spark:** cómputo distribuido para ~406k filas es desproporcionado; sumaría un
  cluster/JVM a una infra ya ajustada.

### Por qué PostgreSQL/RDS sobre las alternativas

- **Sobre DuckDB:** DuckDB es excelente para analítica de un solo proceso, pero es
  **embebido/archivo**: tener a Metabase, la API, DataHub y el pipeline leyendo
  concurrentemente un mismo store se vuelve incómodo. Postgres es un **servidor compartido**
  real en la VPC, y la API ya lo necesita para servir `gold.*` (`database.py`).
- **Sobre BigQuery/Snowflake/Redshift:** un MPP en la nube es sobredimensionado y caro para
  este volumen, agrega egress y saca el dato de la VPC. Un RDS chico alcanza de sobra y
  `dbt-postgres` es de primera clase.
- **RDS (managed) sobre Postgres self-hosted:** backups, parches y alta disponibilidad sin
  que un equipo de 3 los opere a mano.

## Consecuencias

**Positivas:**
- Una sola herramienta SQL cubre Silver→Gold→DQ, en el mismo `dbt build` y el mismo grafo
  de linaje; cero piezas nuevas para calidad o gobierno.
- El `manifest`/`catalog` de dbt alimenta a DataHub sin desarrollo extra (ADR-017).
- Un único motor (Postgres) sirve a BI, API, gobierno y pipeline, todo dentro de la VPC.
- Managed: bajo overhead operativo para el equipo.

**Negativas:**
- Postgres es row-store, no columnar/MPP: a esta escala el full-refresh corre en segundos,
  pero si el volumen creciera mucho, reconstruir todo podría doler (mitigable con modelos
  `incremental` o un motor columnar; ver abajo).
- dbt agrega curva de Jinja/SQL y la dependencia de generar el `manifest` (`dbt parse`)
  antes de cargar el grafo en Dagster (ADR-018).
- RDS tiene un costo base aunque esté ocioso (acotado con una instancia chica).

## Decisiones Técnicas Posteriores

- **Escala:** si el volumen creciera, evaluar modelos `incremental` en la fact (mismo
  análisis del Analytics Engineer para Silver/Gold) o un motor columnar (DuckDB/ClickHouse)
  para la capa analítica.
- **Concurrencia:** si BI + API + gobierno saturan conexiones, sumar pooling (PgBouncer) o
  réplicas de lectura en RDS.
- **Capa semántica:** la definición de métricas hoy vive en Gold (dbt) y parcialmente en
  Metabase; un semantic layer dedicado (dbt Metrics / Cube) queda como bonus (ADR-020).
