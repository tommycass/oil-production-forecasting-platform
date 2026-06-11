# Título: ADR-017: Plataforma de gobierno de datos

**Estado:** Aceptado

## Contexto

La adenda exige una plataforma de gobierno de datos donde se puedan ver: los
**workflows de extracción**, los **datos del data warehouse** y la **última vez
que los datos se actualizaron**. Como requerimiento no funcional, la plataforma
DEBE permitir **navegar el linaje a nivel tabla** y está sugerida explícitamente
**DataHub** ("o alguna herramienta vista en clase/tutoría; pueden explorar
alternativas si está debidamente justificado").

El pipeline ya construido (Fase 2, A y B) nos condiciona la decisión:

- Las transformaciones corren en **dbt sobre Postgres**, y cada `dbt build`
  genera `transform/target/manifest.json` (+ `run_results.json`), que es el
  artefacto estándar del que se deriva el linaje tabla-a-tabla y columna-a-columna.
- La orquestación es **Dagster headless por cron** (sin daemon/UI permanente) en
  cada EC2 (ver [ADR-018](0018-orquestacion-end-to-end-dw.md)).
- Los checks de calidad son **nodos del grafo dbt** ([ADR-016](0016-estrategia-data-quality.md)),
  así que una herramienta que entienda dbt arrastra también las señales de calidad.

La decisión es *con qué herramienta* implementamos gobierno y linaje, dado este stack.

### Evaluación de alternativas

| Criterio (derivado de la consigna y el stack) | DataHub | OpenMetadata | Amundsen | Marquez |
|---|---|---|---|---|
| Ingesta nativa de dbt (manifest/catalog) | Sí, fuente dbt de primera clase | Sí, conector dbt | Parcial (vía dbt extractor de la comunidad) | No (orientado a OpenLineage) |
| Linaje a nivel **tabla** | Sí | Sí | Limitado | Sí (es su foco) |
| Linaje a nivel **columna** | Sí (con `catalog.json`) | Sí | No | No |
| Catálogo + búsqueda para usuarios | Muy completo | Muy completo | Bueno (su foco es discovery) | Mínimo (es lineage, no catálogo) |
| Señales de calidad integradas | Sí (tests dbt como nodos) | Sí (módulo de calidad propio) | No | No |
| Ingesta one-shot tras `dbt build` (orquestación headless) | Sí (recipe CLI / emitters) | Acoplada a su scheduler/workflows | Jobs de databuilder | Eventos OpenLineage en runtime |
| Linaje reproducible desde artefactos (sin tocar el DW) | Sí (lee `target/`) | Parcial (tiende a parsear el warehouse) | Parcial | Vía eventos |
| Peso operativo (contenedores/recursos) | Alto (GMS, frontend, Kafka, ES, almacén de metadata) | Medio-alto (server, ES, MySQL) | Alto (Neo4j/Atlas + ES) | **Bajo** (web + Postgres) |
| Actividad del proyecto / comunidad | Alta | Alta | Baja en los últimos años | Media |

**Marquez** es el más liviano y entraría sin problema en la infra actual, pero es
una herramienta de **linaje vía OpenLineage**, no un catálogo de gobierno: no da
búsqueda rica, ni vista navegable de "datos del DW", ni señales de calidad, y su
integración asume emisión de eventos OpenLineage en runtime — con Dagster *headless*
por cron tendríamos que instrumentar esos eventos a mano, en vez de derivar el linaje
de los artefactos que el pipeline ya produce. **Amundsen** está orientado a *discovery*
y queda corto justo en el requisito central (linaje tabla limitado, sin linaje de
columna), además de arrastrar Neo4j/Atlas.

**OpenMetadata** es el competidor técnico real: cubre catálogo, linaje tabla/columna y
calidad, y su footprint es incluso algo menor. La elección entre ambos se define por el
**encaje con nuestra arquitectura concreta**, no por capacidad nominal:

1. **El linaje se deriva de artefactos, no de la base.** El linaje tabla y columna sale
   de `manifest.json`/`catalog.json` de dbt, que ya se generan en cada `dbt build`. La
   fuente dbt de DataHub consume esos archivos directamente y reconstruye el grafo
   Bronze→Silver→Gold **sin consultar el DW**. Esto pesa porque el RDS vive **encerrado
   en la VPC** (accesible solo por security group): un linaje **reproducible desde
   `transform/target/`** evita depender de parsear el historial de queries del warehouse
   en vivo y de abrir accesos extra a la base.
2. **Ingesta compatible con orquestación headless.** Dagster corre *headless por cron*,
   sin daemon ni scheduler de ingesta permanente. Necesitamos un modelo que se dispare
   como **paso one-shot después del `dbt build`**: DataHub lo resuelve con un recipe por
   CLI (`datahub ingest -c ...`) o con emitters, encajando como un comando más del
   pipeline. El framework de ingesta de OpenMetadata está más acoplado a su propio
   scheduler/workflows, lo que sumaría una pieza viva a un setup diseñado sin daemons.
3. **La calidad de B se reutiliza sin desarrollo extra.** Los 31 tests de dbt
   ([ADR-016](0016-estrategia-data-quality.md)) se mapean a **assertions sobre cada
   dataset** a partir del mismo manifest, exponiendo el gate de calidad a nivel tabla en
   el catálogo de gobierno directamente desde el artefacto que ya emitimos.

El costo que aceptamos es el **peso operativo** (GMS, frontend, Kafka, ES y almacén de
metadata): se absorbe con una instancia dedicada y dimensionada para gobierno, a cambio
del encaje directo con un pipeline cuyo contrato de salida son, justamente, los
artefactos dbt.

## Decisión

Usamos **DataHub** como plataforma de gobierno, desplegado vía **docker-compose**,
con ingesta a través de la **fuente dbt** apuntando a `transform/target/`
(`manifest.json` + `run_results.json`; y `catalog.json` cuando querramos linaje de
columna, agregando un `dbt docs generate`).

- **Linaje end-to-end Bronze→Silver→Gold:** se deriva del grafo dbt; los modelos de
  cada capa y sus dependencias quedan navegables a nivel tabla (y columna con catalog).
- **"Datos del DW" y "última actualización":** la ingesta dbt + (opcional) un conector
  Postgres reflejan las tablas de `gold.*`/`dq.*` y sus timestamps de corrida.
- **Workflows de extracción visibles:** se exponen vía el grafo dbt/manifest; si más
  adelante se levanta el daemon de Dagster, queda disponible el `datahub-dagster-plugin`
  para reflejar el grafo de assets directamente.

### Capacidad

DataHub **no entra** en las EC2 actuales (2–4 GB) junto con la API + monitoreo, así
que se despliega en una **instancia dedicada y más grande**. La capacidad ya está
contemplada (créditos adicionales aprobados por la cátedra) y la infra la gestiona C.
La conexión al RDS requiere habilitar la **regla de security group** del host de
gobierno hacia el RDS (mismo prerrequisito que BI; ver [ADR-020](0020-plataforma-bi.md)).

## Consecuencias

**Positivas:**
- Linaje tabla y columna "gratis" desde los artefactos dbt que ya se generan en cada corrida.
- Las señales de calidad ([ADR-016](0016-estrategia-data-quality.md)) aparecen en el mismo grafo.
- Ingesta desacoplada del DW: el catálogo se reconstruye desde `transform/target/` sin abrir accesos vivos al RDS encerrado en la VPC.

**Negativas:**
- Stack pesado (varios contenedores): obliga a una instancia dedicada y sube el costo/complejidad de deploy.
- La ingesta no es "en vivo": corre como job (manual o agendado) que lee los artefactos dbt; el catálogo refleja la última ingesta, no el estado en tiempo real.
- El linaje de columna exige sumar `dbt docs generate` (genera `catalog.json`), paso que hoy el cron no corre.

## Decisiones Técnicas Posteriores → Implementación (jun-2026)

**Host:** instancia dedicada `governance` — **`t3.large`** (8 GB RAM, $0.083/hr),
Ubuntu 24.04, misma región y VPC que `api`. Se elige `t3.large` sobre `t2.large`:
igual RAM, $0.01/hr más barato, red hasta 5 Gbps y créditos de CPU sin límite
(t3 unlimited burst). No se reutiliza `api-dev` (staging) porque ese host tiene un
ciclo de vida distinto: el CI/CD despliega ahí en cada push a `staging`, lo que
podría interrumpir DataHub durante una demo o corrida de ingesta; además, mezclar
responsabilidades de staging y gobierno en el mismo host elimina el aislamiento de
fallos que justificó elegir una instancia dedicada.

**Despliegue:** vía `datahub docker quickstart` (compose oficial de DataHub, ~8
contenedores: GMS, frontend, OpenSearch, Kafka, MySQL, etc.). Se eligió el quickstart
gestionado por el CLI sobre un compose propio para no mantener sincronizadas las
versiones de cada contenedor; el setup queda en dos comandos (`pip install` +
`datahub docker quickstart`). Detalle operativo en el runbook
[governance-admin](../runbooks/governance-admin.md).

**Linaje de columna: habilitado.** La fuente dbt de DataHub exige `manifest_path` y
`catalog_path`; ambos se proveen. `catalog.json` se genera con `dbt docs generate` y
aporta tipos y descripciones de columna. `run_results.json` se suma como assertions
de los 31 tests de calidad (ADR-016). El recipe quedó en `infra/datahub/dbt_recipe.yml`.

**Carga inicial (bootstrap):** la primera ingesta se corrió desde una build local con
datos de muestra (`seed_sample_bronze.py`), apuntando al GMS remoto. Es legítimo
porque el **linaje y el esquema se derivan de las definiciones de los modelos**, no de
los datos: el grafo Bronze→Silver→Gold resultante es idéntico al de producción. El
flujo recurrente (ingesta desde `api` tras cada `dbt build`) queda documentado en el
runbook; su automatización por cron es mejora futura.

**Regla de SG:** SG dedicado `governance-sg`. Puerto 9002 (frontend) y 8080 (GMS)
abiertos a `0.0.0.0/0` para acceso externo al catálogo y para permitir la ingesta de
bootstrap desde fuera de la VPC; SSH (22) restringido a la IP del administrador. En un
despliegue productivo el 8080 debería restringirse al SG de `api` y el 9002 ir detrás
de TLS.
