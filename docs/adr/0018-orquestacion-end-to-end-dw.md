# Título: ADR-018: Orquestación end-to-end del DW (carga a Postgres y dbt en Dagster)

**Estado:** Aceptado

## Contexto

El ADR-011 eligió **Dagster** como orquestador y modeló el pipeline como assets, pero el
grafo **terminaba en la capa Bronze** (parquet en `data/bronze/`): la carga de Bronze a
Postgres (`transform/scripts/load_bronze.py`) y la construcción de Silver/Gold/Data Quality
(`dbt build`) quedaban **fuera del grafo y sin automatizar** (solo corrían a mano, vía el
runbook del Analytics Engineer). Además, el Data Engineer probó su pipeline solo en local,
por lo que en las EC2 no había datos materializados.

El objetivo de esta fase es dejar el flujo Medallion **andando end-to-end en AWS** (staging y
prod) de modo que BI (tablas `gold.*`) y gobierno (manifest de dbt para DataHub) consuman sin
pasos manuales. Esto obliga a decidir **(1) cómo se integran los modelos dbt al grafo de
Dagster** y **(2) cómo se dispara el pipeline en las EC2**, respetando las restricciones del
entorno: instancias chicas (t2.small/medium, 2–4 GB), un único servicio de API + monitoreo ya
corriendo, y la necesidad de que el mismo código sirva a `oil_dw_staging` y `oil_dw_prod`.

Las transformaciones ya usan **dbt sobre PostgreSQL** (ADR-014/016) y el gate de calidad vive
entre Silver y Gold (un check `error` bloquea Gold, ADR-016): cualquier integración debe
preservar ese bloqueo.

### Evaluación de alternativas — integración de dbt en Dagster

| Criterio | `dagster-dbt` (un asset por modelo) | Asset único que corre `dbt build` (subprocess) | dbt fuera de Dagster (CI/cron directo) |
|---|---|---|---|
| Lineage por modelo en el grafo | Sí (cada modelo = asset; visible en UI y para DataHub) | No (dbt es una caja negra de un solo nodo) | No (desconectado del grafo) |
| Gate de calidad visible | Sí (tests = asset checks; un `error` falla el run) | Parcial (el bloqueo vive dentro de dbt, no en el grafo) | Parcial |
| Manifest de dbt para DataHub | Sí, integrado al flujo | Sí (se genera igual) | Sí |
| Acoplamiento Bronze→Silver en el grafo | Nativo (las dbt sources se ligan a los assets de carga) | Manual (dependencia declarada a mano) | Inexistente |
| Complejidad / dependencias extra | Media (dep `dagster-dbt` + manifest en build) | Baja | Baja |
| Encaje con el objetivo "C no necesita nada más" | Alto (un solo grafo Bronze→Gold→DQ + lineage) | Medio | Bajo |

### Evaluación de alternativas — disparo en AWS

| Criterio | Cron del SO → run headless | Daemon de Dagster + schedule nativo | On-demand (manual) |
|---|---|---|---|
| Footprint de RAM (caja 2–4 GB con API + monitoreo) | Mínimo (proceso efímero por corrida) | Alto (daemon + webserver persistentes) | Mínimo |
| Automatización (sin intervención) | Sí (cron mensual) | Sí (schedule) | No |
| Observabilidad (UI) | No (logs a archivo/`DAGSTER_HOME`) | Sí (UI web) | No |
| Reproducibilidad / simpleza de despliegue | Alta (un script + línea de crontab) | Media (servicio a mantener) | Alta |
| Costo de revertir si se agranda la instancia | Bajo (mismos assets → activar daemon) | — | — |

## Decisión

1. **Integración de dbt con `dagster-dbt`.** Los modelos Silver/Gold se exponen como **un
   asset por modelo** (`@dbt_assets` leyendo el `manifest.json`); los tests de Data Quality
   aparecen como **asset checks** y un check `error` hace fallar el run, preservando el bloqueo
   Silver→Gold del ADR-016. Un `DagsterDbtTranslator` liga las dbt sources `bronze.*` a las
   asset keys de los assets de carga, de modo que el grafo queda conectado
   `Bronze(parquet) → Bronze(Postgres) → Silver → Gold + DQ` de punta a punta.
2. **Carga Bronze→Postgres como assets que reusan `load_bronze.py`.** Dos assets nuevos
   (`bronze/produccion`, `bronze/pozos`) corren el script existente por **subprocess** (con el
   intérprete del propio venv), en lugar de reimplementar la carga o escribir un IO manager: el
   puente parquet→Postgres ya existe, es env-driven y se mantiene en un solo lugar.
3. **Disparo por cron del SO, headless.** Un script `run_pipeline.sh` (`dagster job execute` /
   `asset materialize`) refresca todo Bronze (full reload, ADR-021) y corre la publicación (carga +
   dbt), invocado por un **cron mensual**, sin daemon ni webserver. La fuente es mensual, así
   que esa cadencia alcanza.
4. **Env-driven (staging y prod).** Todo lee `POSTGRES_*` del `infra/.env` de cada EC2; el
   mismo código corre contra `oil_dw_staging` y `oil_dw_prod` cambiando solo `POSTGRES_DB`.

### Por qué `dagster-dbt` sobre el asset-subprocess

El objetivo declarado es que **C (BI/gobierno) no necesite pasos manuales adicionales**.
`dagster-dbt` deja un **único grafo de assets** Bronze→Silver→Gold con el lineage por modelo y
los checks de calidad visibles, que es exactamente lo que alimenta a DataHub y lo que hace
auditable el gate de calidad. El asset-subprocess es más simple pero convierte a dbt en una
caja negra de un nodo, perdiendo el lineage fino y la visibilidad del gate — justo lo que esta
fase necesita exponer.

### Por qué cron headless sobre el daemon

Las EC2 actuales (2–4 GB) ya corren la API y el stack de monitoreo; un daemon + webserver de
Dagster persistente no entra cómodo en esa RAM. El cron headless da la automatización pedida
con footprint casi nulo (proceso efímero por corrida) y es trivial de desplegar (un script y
una línea de crontab). Si la instancia se agranda, se puede volver al daemon + schedule nativo
**sin tocar los assets**.

## Consecuencias

**Positivas:**
- Flujo Medallion completo, automatizado y reproducible en staging y prod; BI y gobierno
  consumen Gold y el manifest sin intervención.
- Un solo grafo de assets con lineage por modelo y el gate de calidad como asset checks:
  materializa la "pata de linaje a DataHub" que el ADR-011 dejaba pendiente.
- Footprint mínimo: no se agrega ningún servicio persistente a las EC2.
- `load_bronze.py` sigue siendo la única fuente de verdad del puente parquet→Postgres.

**Negativas:**
- Sin UI de Dagster en las EC2: la observabilidad se reduce a logs en `DAGSTER_HOME`/archivo
  (mitigable activando el daemon si se agranda la instancia).
- `dagster-dbt` requiere el `manifest.json` generado (`dbt parse`) antes de cargar las
  definiciones, y que el venv tenga el CLI de `dbt` instalado.
- El cron mensual implica que la frescura de Gold depende de esa cadencia (ver ADR-016 sobre
  freshness); un refresh fuera de ciclo es manual (`run_pipeline.sh`).

## Decisiones Técnicas Posteriores

- **Backfill histórico (una vez):** para poblar todo el histórico sin iterar ~150 particiones
  por la UI, se materializa el Bronze en una sola pasada (`extract_produccion_full`) y se corre
  la publicación; el cron mensual usa el camino particionado para todos los meses cerrados
  (full reload, ADR-021).
- **Particiones completas en el refresh:** `run_pipeline.sh` refresca solo meses **cerrados**
  (`MonthlyPartitionsDefinition` con `end_offset=0` no admite el mes en curso).
- **Manifest fuera o dentro del repo:** se mantiene en `transform/target/` (gitignoreado), que
  es donde `dagster-dbt` lo busca; si se relocaliza vía `DBT_TARGET_PATH`, el `DbtProject` debe
  apuntar al mismo path.
- **Coordinación con A:** esta extensión toca la zona de orquestación (ADR-011); se integró por
  PR con su review. La nota correspondiente en ADR-011 referencia este ADR.
- **Coordinación con C:** la ingesta del manifest de dbt a DataHub (lineage) queda de su lado
  (ADR-017); este ADR garantiza que el manifest se produce en cada corrida.
