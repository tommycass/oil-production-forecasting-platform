# Guía de Desarrollo y Contribución

Este documento proporciona las pautas y comandos necesarios para trabajar con el código de este repositorio.

## Resumen del Proyecto

Plataforma de datos para el pronóstico de producción de hidrocarburos. El sistema integra
datos reales de [datos.gob.ar](https://datos.gob.ar) en un pipeline Medallion (Bronze →
Silver → Gold) orquestado con **Dagster**, transforma con **dbt** sobre **PostgreSQL** y
expone los datos a través de:

- **API REST** (FastAPI): `/wells` consulta pozos reales del Data Warehouse (`gold.*`);
  `/forecast` genera un pronóstico sintético de declive lineal para cualquier pozo.
- **Metabase** (BI): exploración de datos `gold.*` para usuarios no técnicos.
- **DataHub** (gobierno): linaje Bronze→Silver→Gold derivado de los artefactos dbt.

---

## Comandos Comunes

### API (directorio `api/`)

```bash
# Instalar dependencias
pip install -r requirements.txt -r requirements-dev.txt

# Levantar la API (requiere API_KEY y las POSTGRES_* del DW para /wells)
API_KEY=tu_clave uvicorn app.main:app --reload

# Linter (coincide con la configuración de CI)
ruff check app/

# Tests de la API
API_KEY=test-key pytest tests/ -v
```

### Pipeline de datos (directorio raíz / `data_pipeline/`)

```bash
# Instalar dependencias del pipeline
pip install -r data_pipeline/requirements.txt

# Levantar Dagster UI (desarrollo local, con venv activado)
dagster dev -m data_pipeline.orchestration.definitions

# Materializar un asset individual
dagster asset materialize -m data_pipeline.orchestration.definitions --select produccion_raw

# Tests del pipeline (sin red ni DB — I/O mockeado)
pytest data_pipeline/tests/ -v
```

### Transformación dbt (directorio `transform/`)

```bash
# Instalar dependencias de dbt
pip install -r data_pipeline/requirements.txt   # incluye dbt-postgres y dbt-expectations

# Parsear modelos y generar manifest.json (necesario antes de correr Dagster)
cd transform && dbt parse --profiles-dir .

# Construir Silver/Gold/DQ (incluye los 31 checks de calidad)
cd transform && dbt build --profiles-dir .

# Generar catalog.json para linaje de columna en DataHub
cd transform && dbt docs generate --profiles-dir .
```

### Docker (desde la raíz del repositorio)

```bash
# Stack base: API + monitoreo
docker compose -f infra/docker-compose.yml up --build

# API:           http://localhost:8000
# Prometheus:    http://localhost:9090
# Grafana:       http://localhost:3000  (admin/admin | visor ext_read/visitor123)
# Alertmanager:  http://localhost:9093
# cAdvisor:      http://localhost:8080

# Agregar base de datos local (Postgres)
docker compose -f infra/docker-compose.yml --profile local-db up

# Postgres DW:   localhost:5432  (user/pass/db: oil/oil/oil_dw)

# Agregar Metabase (BI)
docker compose -f infra/docker-compose.yml --profile bi up

# Metabase:      http://localhost:3001

# Agregar Dagster UI (visualización del grafo de assets)
docker compose -f infra/docker-compose.yml --profile orchestration up

# Dagster UI:    http://localhost:3070
```

### Servicios remotos — EC2 de gobierno (DataHub)

DataHub **no corre en el docker-compose local**; está en una EC2 separada gestionada
por el administrador de gobierno. No se necesita levantar nada local para desarrollar.

| Servicio | URL | Credenciales |
|---|---|---|
| DataHub UI (catálogo) | `http://<ip-governance>:9002` | `datahub / datahub` |
| DataHub GMS (API) | `http://<ip-governance>:8080` | — |

Para el **setup inicial** de la EC2 de gobierno (solo el admin de gobierno):
```bash
# En la EC2 de gobierno (Ubuntu 24.04, ≥8 GB RAM):
chmod +x infra/governance/setup-datahub.sh
./infra/governance/setup-datahub.sh
```

Para **ingerir el linaje** desde los artefactos dbt (ver runbook completo en
`docs/runbooks/governance-admin.md`):
```bash
export DATAHUB_GMS_HOST=<ip-governance>
datahub ingest -c infra/datahub/dbt_recipe.yml
```

### Autenticación de la API

Todos los endpoints, excepto `GET /health` y `GET /metrics`, requieren el header:
```
X-API-Key: <valor de la variable de entorno API_KEY>
```

---

## Arquitectura

### Pipeline de datos (Fase 2)

```
datos.gob.ar → [produccion_raw] → landing (parquet crudo)
                     ↓
             [bronze_produccion]  →  Bronze (parquet particionado por mes)
             [bronze_pozos]       →  Bronze (catálogo de pozos)
                     ↓
             [bronze_*_db]        →  Postgres bronze.*
                     ↓
             [dbt: Silver]        →  Postgres silver.* (limpio, tipado, dedup)
                     ↓  (gate de calidad: 31 tests dbt; error → bloquea Gold)
             [dbt: Gold]          →  Postgres gold.* (modelo estrella)
             [dbt: DQ]            →  Postgres dq.*   (resultados de calidad)
```

El grafo está modelado en Dagster (`data_pipeline/orchestration/assets.py`) y las
transformaciones en dbt (`transform/models/`). En producción lo dispara un cron mensual
(`data_pipeline/orchestration/run_pipeline.sh`); en desarrollo se usa `dagster dev`.

### API REST

```
api/app/
├── main.py          # App factory, registro de routers, Prometheus
├── core/
│   ├── database.py  # Engine SQLAlchemy → gold.* del DW (perezoso, env-driven)
│   └── security.py  # Dependencia verify_api_key()
├── routes/          # health, wells, forecast
├── services/
│   ├── wells.py     # Consulta gold.fact_produccion_mensual → IDs reales del DW
│   └── forecast.py  # Genera pronóstico sintético (acepta cualquier ID de /wells)
└── schemas/         # Modelos Pydantic de respuesta
```

`/wells` consulta el DW real (gold); `/forecast` genera datos sintéticos y acepta
cualquier ID devuelto por `/wells`, incluyendo IDs numéricos reales de la fuente.

### Stack de Monitoreo

- Prometheus scrapea `/metrics` cada 15s; reglas de alerta en `monitoring/alerts.yml`.
- Grafana lee de Prometheus; dashboards provisionados en `monitoring/grafana/`.
- El endpoint `/metrics` está **excluido** del instrumentador de Prometheus (ADR-003).

### CI/CD

GitHub Actions (`.github/workflows/ci.yml`) ejecuta jobs secuenciales:

1. **test**: `ruff check api/app/` + `pytest api/tests/` (tests de la API).
2. **test-pipeline**: `pytest data_pipeline/tests/` (contracts del pipeline, sin red).
3. **build** (solo si `test` y `test-pipeline` pasan): build Docker → Trivy → ECR.
4. **deploy** (en merge a `staging` o `main`): SSM → EC2 (`api-dev` o `api`), con
   rollback automático si `/health` falla.

### Git Workflow

GitFlow: ramas de features → `staging` → `main` (siempre vía PR).
Nomenclatura: `feature/`, `fix/`, `docs/`, `fase2/<persona>-<tema>`.

---

## Restricciones Clave

- `API_KEY` debe estar configurada para levantar la API y para los tests.
- `POSTGRES_*` deben apuntar al DW (RDS en EC2, o Postgres local) para que `/wells`
  funcione; `/forecast` no requiere DB.
- El contexto de build de Docker es `api/`, pero el Dockerfile está en `infra/`:
  `docker build -f infra/Dockerfile api/`.
- `ruff` se aplica solo sobre `api/app/` (se excluye `tests/`).
- Los tests del pipeline (`data_pipeline/tests/`) mockean la red y el filesystem;
  corren sin Postgres ni descarga de datos reales.

## Consigna y requerimientos

Ver `docs/adenda_tecnica_fase2.md` para los requerimientos de Fase 2. Las decisiones de
diseño están en `docs/adr/`. La documentación del modelo de datos está en `docs/data-model.md`.
