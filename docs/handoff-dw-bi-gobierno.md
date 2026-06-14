# Hands-off — Data Warehouse para BI y Gobierno

Documento de traspaso del **Analytics Engineer** al **administrador de BI y gobierno**.
Cubre qué quedó construido, cómo conectarse, dónde están los datos, los contratos de cada
capa, cómo se refresca y qué falta de tu lado para continuar (Metabase + DataHub).

Referencias: [data-model.md](data-model.md) (contrato Gold en detalle), [runbook del
Analytics Engineer](runbooks/analytics-engineer.md) (operación), ADR-014/015/016/018/019.

---

## 1. Qué quedó construido (resumen ejecutivo)

El flujo Medallion corre **end-to-end, automatizado y reproducible en staging y prod**:

```
data.gob.ar (2 CSV)  →  Bronze (parquet)  →  bronze.* (Postgres)  →  silver.*  →  gold.* (estrella) + dq.*  →  semantic.*
        Data Engineer                             Analytics Engineer — orquestado en Dagster (cron mensual)             BI / API
```

- Orquestado con **Dagster** (grafo de assets Bronze→Postgres→Silver/Gold/DQ→Semantic), disparado por
  **cron mensual headless** en cada EC2 (ADR-018).
- **Gold** (modelo estrella) ya está **poblado y consultable** en ambos entornos:
  **405.993 filas** en `gold.fact_produccion_mensual` (dataset real completo, producción no
  convencional).
- **Data Quality** corre en cada build: 31 checks, resultados persistidos en `dq.dq_results`,
  filas inválidas en cuarentena (`dq.silver_produccion_rechazos`), gate `error` que bloquea Gold.
- **Manifest de dbt** generado en cada corrida → insumo para el lineage en DataHub.

Lo que tenés que hacer vos arranca en la sección 9.

---

## 2. Cómo conectarte al DW

**Una sola instancia RDS PostgreSQL 16, dos bases** (aislamiento dentro del free tier):

| Entorno | Base | EC2 (tag `Name`) |
|---|---|---|
| Producción | `oil_dw_prod` | `api` |
| Staging | `oil_dw_staging` | `api-dev` |

```
host:  oil-dw-prod.cnm68se8k08m.us-east-2.rds.amazonaws.com
port:  5432
user:  oil_admin
pass:  (NO está en el repo) → infra/.env de la EC2 (chmod 600) o el gestor de contraseñas del equipo
db:    oil_dw_prod  (prod)  |  oil_dw_staging (staging)
```

### Modelo de conectividad (importante)

El RDS **no es accesible desde fuera de la VPC**: su security group solo admite tráfico desde
el **SG de las EC2** (no por IP, por SG). Implicancias para vos:

- **Tu laptop NO llega directo al RDS.** Para una consulta puntual, usá un túnel SSH/SSM a
  través de la EC2, o `psql` desde la propia EC2.
- **Metabase/DataHub deben correr dentro de la VPC** (en una EC2 con el SG permitido, o se
  agrega una regla en el SG del RDS que admita el SG del host de BI). Esa **regla de SG es lo
  primero que vas a necesitar** (ver sección 9).

Prueba rápida de conexión (desde una EC2 del proyecto):
```bash
PGPASSWORD=... psql -h oil-dw-prod.cnm68se8k08m.us-east-2.rds.amazonaws.com \
  -U oil_admin -d oil_dw_prod -c '\dn'   # lista los esquemas: bronze, silver, gold, semantic, dq
```

---

## 3. Dónde están los datos (esquemas y contratos)

Cinco esquemas por base (cuatro capas Medallion + capa semántica):

| Esquema | Contenido | ¿Lo consume BI? |
|---|---|---|
| `bronze` | Crudo de la fuente como **texto** (`bronze.produccion`, `bronze.pozos`). Inmutable. | No — solo trazabilidad/debug |
| `silver` | Limpio y tipado: `silver_produccion`, `silver_pozos`. | Raramente (drill-down) |
| **`gold`** | **Modelo estrella** — lo que consume BI/API (avanzado). | **Sí** |
| **`semantic`** | **Vistas semánticas** sobre Gold pre-unificadas (ADR-027). Contrato recomendado para usuarios de BI sin conocimiento del modelo estrella. | **Sí (preferido para BI no técnico)** |
| `dq` | Señales de calidad: `dq_results`, cuarentena, `store_failures`. | Sí (tableros de calidad / gobierno) |

### 3.1 Gold (estrella) — el contrato principal para BI

> Detalle completo (columnas, surrogate keys, SCD, aditividad) en **[data-model.md](data-model.md)**.

- **Fact:** `gold.fact_produccion_mensual` — grano **(pozo, mes)** = `idpozo + anio + mes`.
  Medidas aditivas: `prod_pet`, `prod_gas`, `prod_agua`, `iny_*`. **`tef` NO es aditiva**
  (promediar, no sumar). FKs: `sk_pozo`, `sk_operadora`, `sk_yacimiento`, `sk_fecha`
  (+ `idpozo` como dimensión degenerada para drill-down).
- **Dims:** `dim_pozo`, `dim_operadora`, `dim_yacimiento`, `dim_fecha`. Surrogate keys `sk_*`.
  **SCD Type 1** (sobrescritura) en pozo/operadora. Cada dim tiene un **miembro `sk = -1`**
  ("desconocido") para FKs no resueltas — al joinear, ninguna fila de la fact se pierde.
- **Join canónico para un dashboard:**
  ```sql
  select d.periodo, o.operadora, sum(f.prod_pet) as petroleo, sum(f.prod_gas) as gas
  from gold.fact_produccion_mensual f
  join gold.dim_fecha     d on d.sk_fecha     = f.sk_fecha
  join gold.dim_operadora o on o.sk_operadora = f.sk_operadora
  group by 1, 2 order by 1;
  ```

### 3.2 `dq` — señales de calidad y gobierno

- **`dq.dq_results`** — audit-trail de **todos** los checks por corrida:
  `invocation_id, test_name, dimension, severity, status, failures, executed_at`.
  Última corrida, resumen por estado:
  ```sql
  select status, count(*) from dq.dq_results
  where invocation_id = (select invocation_id from dq.dq_results order by executed_at desc limit 1)
  group by status;
  ```
- **`dq.silver_produccion_rechazos`** — **cuarentena**: filas excluidas de Silver por validez
  dura (producción negativa), con `motivo_rechazo` y timestamp. Reconcilia `bronze = silver +
  rechazos` (ADR-019). Hoy: 3 filas.
- **`dq.<nombre_test>`** — `store_failures`: filas ofensoras de cada test que falla (vacías si
  todo pasa).

Estas tres son la materia prima para un **tablero de calidad** en Metabase y para señales de
calidad a nivel tabla en DataHub.

---

## 4. Para BI (Metabase)

1. Conectá Metabase a la base **`oil_dw_prod`** (staging para preview/QA).
2. Para usuarios no técnicos: modelá sobre el esquema **`semantic`** — las cuatro vistas
   (`sem_produccion_mensual_por_yacimiento`, `sem_top_pozos`, `sem_kpi_pipeline`,
   `sem_produccion_anual_por_operadora`) ya aplican los joins y exponen nombres en lenguaje
   de negocio sin surrogate keys. Ver [ADR-027](adr/0027-semantic-layer.md).
3. Para análisis avanzados: modelá sobre **`gold`** (la estrella ya está lista). Cuidados: sumar
   solo medidas aditivas; `tef` se promedia; usá `dim_fecha.periodo` (`AAAA-MM`) para ejes
   temporales; los joins van por `sk_*`.
4. (Opcional) un dashboard de calidad sobre `dq.dq_results` + `dq.silver_produccion_rechazos`.

---

## 5. Para Gobierno (DataHub) — lineage

El pipeline produce el **`manifest.json` de dbt** en cada corrida, que es el insumo estándar
para el lineage tabla-a-tabla y columna-a-columna en DataHub.

- **Dónde está:** `transform/target/manifest.json` en cada EC2 (regenerado por `dbt parse` y por
  cada `dbt build`). Gitignoreado (es efímero).
- **Recomendado:** usar la **fuente de ingesta dbt de DataHub** apuntando a los artefactos de
  `transform/target/` (`manifest.json`, `run_results.json`). Para lineage **a nivel columna** y
  descripciones, agregá un `dbt docs generate` (produce `catalog.json`) — hoy el cron corre
  `dbt build` (genera manifest + run_results, no catalog); si querés catalog, se agrega un paso
  o lo corrés on-demand.
- **Alternativa:** el grafo de assets de Dagster (vía `datahub-dagster-plugin`). La UI de Dagster
  está activa en `api` (producción) en el puerto 3070 (servicio systemd, ver
  [ADR-023](adr/0023-ui-dagster-containerizada.md)); si se instala el plugin de DataHub para
  Dagster, el lineage puede ingestarse también desde ahí.
- El gate de calidad (tests dbt) son **nodos del manifest**, así que las señales de calidad
  también viajan a DataHub.

> **Caveat de capacidad:** DataHub es pesado y **no entra en las EC2 actuales (2–4 GB)** junto
> con la API + monitoreo. Va a necesitar una instancia aparte / más grande. Esto ya estaba
> anotado como riesgo; es decisión tuya + infra (A/C).

---

## 6. Cómo se refresca el DW (orquestación)

- **Automático:** `cron` mensual en cada EC2 (`0 3 5 * *`) corre
  `data_pipeline/orchestration/run_pipeline.sh`, que recarga todo Bronze (full reload, ADR-021), lo
  carga a Postgres y corre `dbt build` (Silver/Gold/DQ). Log en `~/dagster-runtime/cron.log`.
- **Determinismo:** Silver/Gold son `table` full-refresh → cada corrida reconstruye el mismo Gold
  (idempotente). BI siempre lee el último Gold materializado.
- **Refresh manual** (si necesitás datos frescos fuera de ciclo), desde la EC2:
  ```bash
  cd /home/ubuntu/oil-production-forecasting-platform
  bash data_pipeline/orchestration/run_pipeline.sh
  ```
- **Si un check `error` falla**, el run aborta y **Gold no se actualiza** (queda servido el Gold
  anterior, intacto): BI nunca consume datos rotos. El detalle del fallo queda en `dq.dq_results`
  y en la tabla de `store_failures` correspondiente (ver runbook §5).

---

## 7. Entornos

| | Staging | Prod |
|---|---|---|
| Base | `oil_dw_staging` | `oil_dw_prod` |
| EC2 | `api-dev` | `api` |
| Uso sugerido | preview/QA de tableros y de la ingesta a DataHub | BI/gobierno productivo |

Mismo esquema y mismos contratos en ambas. Apuntá producción a `oil_dw_prod`.

---

## 8. Calidad de datos (qué garantiza el gate)

- Dimensiones cubiertas: schema, completeness, validity, uniqueness, freshness (ADR-016).
- Invariantes que **bloquean** Gold (`severity: error`): unicidad de `idpozo+anio+mes`, no-nulos
  de claves, integridad referencial fact→dim.
- Suciedad irreparable de origen (producción negativa) → **cuarentena**, no bloqueo (ADR-019).
- Frescura: `dbt source freshness` avisa (`warn`) si la última ingesta supera el umbral.

---

## 9. Lo que queda de tu lado (asks para el administrador de BI y gobierno)

1. **Regla de SG para BI/gobierno:** habilitar que el host de Metabase/DataHub llegue al RDS
   (agregar el SG de ese host al security group del RDS). Sin esto, las herramientas no conectan.
2. **Metabase** apuntando a `oil_dw_prod`, modelando preferentemente sobre `semantic.*` para usuarios no técnicos y `gold.*` para consultas avanzadas (+ opcional dashboard de `dq.*`).
3. **DataHub:** decidir host (instancia aparte por capacidad), configurar la ingesta dbt desde
   `transform/target/manifest.json` (+ `dbt docs generate` si querés lineage de columnas).
4. **Retención de tablas `dq.*`** (`dq_results`, `store_failures`, cuarentena): definir rotación
   si crecen (hoy `dq_results` acumula por corrida).
5. **Secciones del README** (BI, gobierno, arquitectura de datos) son tuyas — no las tocamos para
   evitar conflictos.

### Caveats conocidos
- **Capacidad:** DataHub no entra en las EC2 de 2–4 GB → instancia aparte/más grande.
- **`calogica/dbt_expectations` deprecado** → migrar a `metaplane/dbt_expectations` a futuro (no
  bloquea; warning en cada corrida).
- **Provisioning de la EC2 de prod** tenía gaps que ya resolvimos (faltaba `infra/.env`,
  `python3.12-venv`, y el disco root era de 7 G → ampliado a 20 G); conviene sumarlos al baseline.

---

## 10. Referencias

- Contrato Gold detallado: [data-model.md](data-model.md)
- Operación del pipeline: [runbook Analytics Engineer](runbooks/analytics-engineer.md) §3.1
- Decisiones: ADR-014 (Medallion), ADR-015 (estrella), ADR-016 (Data Quality),
  ADR-018 (orquestación end-to-end), ADR-019 (cuarentena). DataHub/gobierno: ADR-017 (tuyo).
