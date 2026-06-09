# Título: ADR-011: Elección de la herramienta de orquestación

**Estado:** Aceptado

## Contexto

La Fase 2 exige una herramienta de orquestación con **DAGs definidos como código** para correr el pipeline de ingesta y procesamiento (Data Sources → Bronze → Silver → Gold). La herramienta debe habilitar los requisitos de la adenda técnica: idempotencia (re-ejecutar no duplica), retries con backoff exponencial (la extracción depende de descargas HTTP que pueden fallar), observabilidad mínima (logs y status accesibles), backfill por fecha y emisión de linaje hacia la plataforma de gobierno (DataHub, ADR-017).

El backfill merece atención especial: el dataset de producción incluye una columna `rectificado` y la fuente corrige meses ya publicados, por lo que reprocesar un mes puntual sin afectar el resto no es opcional, es central a nuestro caso.

Se evaluaron tres alternativas open-source con DAGs como código: **Apache Airflow**, **Prefect** y **Dagster**. Se descartó la opción de scripts con `cron`, que no cubre idempotencia, retries, observabilidad ni backfill sin reimplementar a mano lo que estas herramientas ya proveen.

### Evaluación de alternativas frente a la consigna

| Criterio (de la consigna) | Airflow | Prefect | Dagster |
|---|---|---|---|
| DAGs como código | Sí | Sí | Sí |
| Idempotencia | Manual (responsabilidad del task) | Manual | Nativa (assets y particiones) |
| Retries con backoff exponencial | Sí | Sí | Sí |
| Observabilidad (UI con logs/status) | Sí, robusta | Sí, simple | Sí, con grafo de assets |
| Backfill / reprocesar por fecha | Nativo (`dags backfill`) | Manual (parametrizar el flow) | Nativo (assets particionados) |
| Lineage hacia DataHub | Soporte nativo | Vía plugin | Vía plugin (`datahub-dagster-plugin`) |
| Overhead operativo | Alto (scheduler, webserver, metadata DB, worker) | Bajo | Medio (un servicio y su UI) |
| Curva de aprendizaje | Media-alta | Baja | Media |
| Encaje con Medallion | Neutro (orquesta tareas) | Neutro (orquesta tareas) | Alto (orquesta datasets) |

Los tres cumplen lo básico (DAGs, retries, observabilidad); la diferencia aparece en los requisitos que más pesan en esta fase y en nuestras restricciones. **Airflow** cubre todo e incluso ofrece el mejor linaje nativo hacia DataHub, pero su overhead (varios contenedores y una metadata DB) es desproporcionado. **Prefect** es el de menor fricción, pero su debilidad está justo en los dos requisitos centrales de nuestro caso: el backfill no es de primera clase y la idempotencia queda enteramente en manos de quien escribe el task. **Dagster** modela el pipeline como assets particionados por `anio/mes`, lo que convierte el backfill de un mes corregido en una operación nativa y expresa la arquitectura Medallion directamente en código, a costa de una curva algo mayor y de requerir un plugin para el linaje.

## Decisión

Usaremos **Dagster** como herramienta de orquestación, modelando el pipeline como **software-defined assets** particionados por mes (`anio/mes`).

### Por qué Dagster sobre Prefect

- **Backfill nativo por partición.** Reprocesar una fecha puntual —obligatorio en la consigna y forzado por la columna `rectificado`— se resuelve re-materializando la partición correspondiente, sin escribir lógica de rangos de fechas a mano. En Prefect deberíamos construir y mantener ese mecanismo nosotros.
- **Idempotencia y encaje con Medallion.** Al declarar Bronze/Silver/Gold como assets con dependencias, Dagster materializa de forma determinística y conoce qué depende de qué. El modelo de assets es la arquitectura Medallion expresada en código, lo que reduce el código pegamento que Prefect (orientado a tareas) nos obligaría a escribir.
- **Linaje interno visible.** El grafo de assets ya muestra el flujo Bronze→Silver→Gold en su UI, lo que refuerza la gobernanza incluso antes de integrar DataHub.

### Por qué Dagster sobre Airflow

- **Overhead acorde al equipo.** Airflow exige scheduler, webserver, base de metadatos y típicamente un worker: varios contenedores que sumar al `docker-compose` y mantener entre 3 personas en 2 semanas. Dagster corre con un único servicio y su UI, suficiente para nuestra escala.
- **El lineage nativo de Airflow no compensa.** Su principal ventaja para nosotros sería la integración con DataHub, pero ese linaje también lo obtendremos por el plugin de Dagster y por la ingesta de metadata desde el propio DW que hará la Persona C. No justifica el costo operativo.

El tipo de carga por fuente (full refresh para el catálogo de pozos; full refresh materializado por partición `anio/mes` para producción, con la resolución de meses corregidos —merge/upsert por `idpozo + anio + mes`— diferida a Silver) se implementa sobre el modelo de particiones de Dagster y se justifica en el ADR-012.

## Consecuencias

**Positivas:**
- Backfill e idempotencia resueltos de forma nativa, cubriendo los requisitos más ponderados de la fase sin código a medida.
- El grafo de assets documenta y visualiza la arquitectura Medallion, aportando linaje interno desde el inicio.
- Overhead operativo contenido (un servicio y su UI) frente a Airflow, compatible con el `docker-compose` actual.
- Retries con backoff y logs/status accesibles vía la UI, cumpliendo observabilidad.

**Negativas:**
- Curva de aprendizaje mayor que Prefect: el equipo debe entender el modelo de assets, particiones e IO managers.
- El linaje hacia DataHub no es nativo: requiere configurar el `datahub-dagster-plugin` (mismo costo que Prefect; Airflow lo habría dado de forma más directa).
- Menor masa de tutoriales y operadores que Airflow para casos no estándar, sin impacto en el alcance de esta fase.

## Decisiones Técnicas Posteriores

- **Coordinación con gobierno:** confirmar la ruta de emisión de linaje a DataHub (plugin de Dagster o ingesta desde el DW). Es la única pata de esta decisión que no es exclusiva del Data Engineer.
- **Particionado:** producción se modela en **dos assets** para aprovechar el backfill nativo por mes sin pagar la descarga completa por partición. Como la fuente publica un único archivo (no permite bajar un mes puntual), un primer asset `produccion_raw` descarga el CSV completo **una vez** a una zona de landing; un segundo asset `bronze_produccion`, **particionado por mes** (`MonthlyPartitionsDefinition`), lee de ese landing y escribe solo la partición de su mes. Así, reprocesar un mes corregido es materializar esa partición desde la UI de Dagster: reescribe únicamente ese mes, sin re-descargar ni tocar el resto (ver ADR-012 y ADR-013).
- **Persistencia:** IO manager hacia el data warehouse (cuya elección corresponde al Analytics Engineer, ver su ADR) y archivos parquet en la capa Bronze (`data/bronze/`, ya versionada como estructura y con los datos gitignoreados).
- **Despliegue:** Dagster se suma como servicio en el `docker-compose` existente, coordinando con Infraestructura.

## Actualización (jun-2026) — el grafo se extiende al DW y corre en AWS

> Propuesta del Analytics Engineer (Persona B), **pendiente de review del Data Engineer**
> (dueño de este ADR). Toca la zona de orquestación, por eso se documenta acá.

Para dejar el flujo Medallion andando end-to-end en AWS (objetivo: que BI/gobierno
consuman Gold sin pasos manuales), el grafo de assets **se extiende más allá de Bronze**:

- **Bronze→Postgres:** dos assets nuevos (`bronze/produccion`, `bronze/pozos`) cargan el
  parquet al esquema `bronze` del DW corriendo `transform/scripts/load_bronze.py`.
- **Silver/Gold/DQ:** los modelos dbt se integran con **`dagster-dbt`** (un asset por
  modelo); los tests de Data Quality aparecen como **asset checks** y un check `error`
  hace fallar el run (mantiene el bloqueo Silver→Gold del ADR-016). Esto materializa la
  "pata de linaje" que el ADR dejaba pendiente: el manifest de dbt alimenta a DataHub.

**Ejecución (ajuste al despliegue original).** En lugar de levantar Dagster como servicio
persistente en `docker-compose` (daemon + webserver), en las EC2 actuales (t2.small, 2 GB)
se dispara **headless por cron del SO** vía `data_pipeline/orchestration/run_pipeline.sh`
(`dagster job execute`/`asset materialize`), sin daemon ni UI. Motivo: la caja no tiene RAM
para un servicio persistente además de la API y el monitoreo. Si la instancia se agranda,
se puede volver al daemon + schedule nativo sin cambiar los assets.

**Env-driven (staging y prod).** Todo lee `POSTGRES_*` del `infra/.env` de cada EC2, así el
mismo código corre contra `oil_dw_staging` y `oil_dw_prod` cambiando solo esa variable.

Procedimiento operativo (setup del venv, swap/instancia, backfill histórico, cron, rollout
a prod): ver el runbook del Analytics Engineer.

La **decisión formal con comparación de alternativas** de esta extensión (integración de dbt
vía `dagster-dbt` vs asset-subprocess; disparo por cron headless vs daemon) está en el
**[ADR-018](0018-orquestacion-end-to-end-dw.md)**.
