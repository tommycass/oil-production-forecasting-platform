# transform — capas Silver y Gold (dbt)

Proyecto **dbt** que transforma la capa Bronze en las capas **Silver** (limpieza) y
**Gold** (modelo estrella) del Data Warehouse PostgreSQL, y corre el framework de
**Data Quality**. Zona del Analytics Engineer.

- Decisiones: [ADR-014 Medallion](../docs/adr/0014-arquitectura-medallion.md) ·
  [ADR-015 estrella](../docs/adr/0015-modelo-dimensional-estrella.md) ·
  [ADR-016 Data Quality](../docs/adr/0016-estrategia-data-quality.md)
- Modelo de datos detallado: [`docs/data-model.md`](../docs/data-model.md)

## Estructura

```
transform/
  dbt_project.yml          config dbt (esquemas por capa, store_failures, on-run-end)
  profiles.yml             conexión a Postgres por env vars
  packages.yml             dbt_utils + dbt_expectations
  macros/
    generate_schema_name.sql   esquemas limpios (silver/gold/dq)
    log_dq_results.sql         persiste cada check en dq.dq_results
  models/
    bronze/_bronze_sources.yml  fuentes (bronze.produccion, bronze.pozos)
    silver/                     silver_produccion, silver_pozos + tests de calidad
    gold/                       dim_* , fact_produccion_mensual + tests de integridad
  tests/
    assert_freshness_produccion.sql   check de freshness (warn)
  scripts/
    load_bronze.py           parquet de data/bronze/ → Postgres (handoff de A)
    seed_sample_bronze.py    muestra de las fuentes reales para dev/test
```

## Capas y esquemas en Postgres

| Capa | Esquema | Contenido |
|------|---------|-----------|
| Bronze | `bronze` | crudo cargado como texto (lo provee A / `load_bronze.py`) |
| Silver | `silver` | `silver_produccion`, `silver_pozos` (limpio, tipado) |
| Gold | `gold` | `fact_produccion_mensual` + `dim_pozo/operadora/yacimiento/fecha` |
| Data Quality | `dq` | `dq_results` (audit de checks) + tablas de `store_failures` |

## Cómo correr (local)

Requiere el container Postgres del docker-compose levantado (servicio `postgres`).

```bash
# 1. Levantar el DW
docker compose -f infra/docker-compose.yml up -d postgres

# 2. Entorno e instalación
cd transform
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 3. Variables de conexión (defaults del compose)
export POSTGRES_HOST=localhost POSTGRES_PORT=5432 \
       POSTGRES_USER=oil POSTGRES_PASSWORD=oil POSTGRES_DB=oil_dw

# 4. Instalar paquetes dbt
dbt deps

# 5. Cargar Bronze
#    Opción A (datos reales, tras la extracción de A):
python scripts/load_bronze.py
#    Opción B (muestra para dev/test, sin esperar la extracción):
python scripts/seed_sample_bronze.py --rows 2000

# 6. Construir Silver + Gold y correr Data Quality
dbt build --profiles-dir .
```

`dbt build` materializa los modelos **y** corre los tests en orden de dependencia:
si un test crítico (`severity: error`) sobre Silver falla, **dbt aborta antes de
materializar Gold** → la promoción Silver→Gold queda bloqueada (ADR-016).

## Data Quality

- **5 dimensiones cubiertas**: schema, completeness, validity, uniqueness, freshness.
- **Persistencia**: `dq.dq_results` (un registro por check y por corrida, con estado y
  nº de fallas) + tablas `dq.<test>` con las filas ofensoras (`store_failures`).
- **Consecuencia operativa**: los checks `error` bloquean Gold; los `warn` solo registran.

```sql
-- Último estado de calidad
select dimension, status, count(*)
from dq.dq_results
where invocation_id = (select invocation_id from dq.dq_results order by executed_at desc limit 1)
group by dimension, status;
```

## Comandos útiles

```bash
dbt build --profiles-dir .                 # todo (models + tests)
dbt run   --profiles-dir . --select silver # solo Silver
dbt test  --profiles-dir . --select gold   # solo tests de Gold
dbt source freshness --profiles-dir .      # freshness de Bronze
dbt docs generate --profiles-dir . && dbt docs serve --profiles-dir .  # linaje
```
