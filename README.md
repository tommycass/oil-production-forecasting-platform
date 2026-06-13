# Oil Production Forecasting Platform

Plataforma Predictiva de Producción de Hidrocarburos — Trabajo Integrador de Ingeniería de Software.

El sistema integra datos reales de producción de hidrocarburos (datos.gob.ar) en una **plataforma de datos** sobre arquitectura Medallion: ingesta orquestada con **Dagster**, transformación con **dbt** (Bronze → Silver → Gold con modelo estrella), checks de **calidad de datos** persistidos, exploración para usuarios de negocio en **Metabase** (BI) y linaje/gobierno en **DataHub**. Por encima, expone una **API REST** que sirve esos datos, con infraestructura reproducible con Docker, pipeline de **CI/CD** y monitoreo con **Prometheus y Grafana**.

> **Fase 1** construyó la API, la infraestructura (Docker/AWS), el CI/CD y el monitoreo. **Fase 2** (esta entrega) agrega la integración de datos: pipeline Medallion, DW dimensional, calidad de datos, BI y gobierno. La arquitectura de datos se describe en [Arquitectura de datos](#arquitectura-de-datos).

---

## Servicio desplegado

| Ambiente | IP |
|---|---|
| Producción | 18.117.126.59 |
| Staging | _No Disponible_ |

**Documentación interactiva (Swagger UI):** http://18.117.126.59:8000/docs

> Todas las rutas bajo `/api/v1/` requieren autenticación por API key (ver sección [Autenticación](#autenticación)). Se debe completar la ruta con IP:PORT/RUTA (ver sección Servicios expuestos en el host)

---

## Integrantes

 - Michanie Micol
 - Pettazi Valentino 
 - Castro Tomás 

---

## Estructura del Proyecto

```
oil-production-forecasting-platform/
│
├── .github/
│   └── workflows/
│       └── ci.yml                  # Pipeline de CI/CD (GitHub Actions)
│
├── api/
│   ├── app/
│   │   ├── core/                   # Lógica transversal
│   │   │   ├── security.py         # Middleware de validación de API key (X-API-Key)
│   │   │   ├── rate_limit.py       # Configuración de rate limiting (SlowAPI)
│   │   │   └── demo_data.py        # Datos mock de pozos y producción base
│   │   ├── routes/                 # Endpoints de la API
│   │   │   ├── health.py           # GET /health
│   │   │   ├── wells.py            # GET /api/v1/wells
│   │   │   ├── forecast.py         # GET /api/v1/forecast
│   │   │   └── mock_error.py       # GET /mock-500 (testing)
│   │   ├── schemas/                # Schemas Pydantic (request/response)
│   │   ├── services/               # Lógica de negocio y generación de datos mock
│   │   ├── __init__.py
│   │   └── main.py                 # Punto de entrada de la aplicación FastAPI
│   ├── tests/                      # Tests unitarios y de integración (pytest)
│   ├── .env.example                # Variables de entorno requeridas (ej. API_KEY)
│   ├── requirements.txt            # Dependencias de runtime
│   ├── requirements-dev.txt        # Dependencias de desarrollo (pytest, ruff, etc.)
│   └── README.md
│
├── data_pipeline/                  # Zona Data Engineer: extracción + Bronze + orquestación
│   ├── config.py                   # URLs de las fuentes + rutas (landing/Bronze) + contrato de schema
│   ├── extraction/                 # Extracción de las 2 fuentes datos.gob.ar
│   │   ├── extract_pozos.py
│   │   ├── extract_produccion.py
│   │   └── validation.py           # Validación de schema en la ingesta (fail-fast, ADR-022)
│   ├── orchestration/              # Assets de Dagster (orquestación)
│   │   ├── assets.py               # Bronze(parquet) → Bronze(Postgres) → dbt (Silver/Gold/DQ)
│   │   ├── definitions.py          # Punto de entrada + job dw_publish
│   │   ├── dbt_project.py          # Proyecto dbt expuesto a dagster-dbt
│   │   └── run_pipeline.sh         # Refresh headless para cron (full reload, env-driven)
│   ├── tests/                      # Tests del pipeline (pytest)
│   ├── requirements.txt
│   └── requirements-dev.txt
│
├── data/                           # Datos crudos (gitignored): landing + capa Bronze
│
├── transform/                      # Zona Analytics Engineer: proyecto dbt (Silver/Gold/DQ)
│   ├── dbt_project.yml             # Configuración del proyecto dbt
│   ├── profiles.yml                # Conexión al DW Postgres (env-driven: POSTGRES_*)
│   ├── models/
│   │   ├── bronze/                 # dbt sources de bronze.* (entrada del modelo)
│   │   ├── silver/                 # Limpieza, tipado, dedup + cuarentena de rechazos
│   │   ├── gold/                   # Modelo estrella: fact_produccion_mensual + 4 dims
│   │   └── semantic/               # Vistas semánticas sobre Gold para BI (ADR-027)
│   ├── macros/
│   │   └── log_dq_results.sql      # Persiste los checks de calidad en dq.dq_results
│   ├── scripts/
│   │   ├── load_bronze.py          # Puente parquet → bronze.* (Postgres)
│   │   └── seed_sample_bronze.py   # Bronze de muestra para pruebas/bootstrap
│   └── tests/                      # Tests dbt singulares (p. ej. freshness)
│
├── docs/
│   ├── consigna-fase1.md
│   ├── consigna-fase2.md
│   ├── adenda_tecnica_fase2.md
│   ├── data-model.md               # Contrato Gold: grano, dims, surrogate keys, SCD
│   ├── handoff-dw-bi-gobierno.md   # Traspaso del DW de B a C (BI + gobierno)
│   ├── runbooks/                   # Runbooks por rol
│   │   ├── data-engineer.md        # Reprocesar un mes corregido por la fuente
│   │   ├── analytics-engineer.md   # Reconstruir Silver/Gold y resolver gate de calidad
│   │   ├── bi-user.md              # Explorar y analizar producción en Metabase
│   │   └── governance-admin.md     # Desplegar DataHub y ejecutar la ingesta dbt
│   └── adr/                        # Architecture Decision Records
│       ├── 0001-framework-backend.md
│       ├── 0002-docker-containerizacion.md
│       ├── 0003-prometheus-grafana-monitoreo.md
│       ├── 0004-alertmanager-slack-notificaciones.md
│       ├── 0005-cloudwatch-monitoreo-ec2.md
│       ├── 0006-limpieza-disco-ec2.md
│       ├── 0007-rate-limiting-api.md
│       ├── 0008-operational-endpoints.md
│       ├── 0009-testing-strategy-api.md
│       ├── 0010-api-key-validation-strategy.md
│       ├── 0011-orquestador.md
│       ├── 0012-tipo-de-carga.md
│       ├── 0013-diseno-capa-bronze.md
│       ├── 0014-arquitectura-medallion.md
│       ├── 0015-modelo-dimensional-estrella.md
│       ├── 0016-estrategia-data-quality.md
│       ├── 0017-plataforma-gobierno-datos.md
│       ├── 0018-orquestacion-end-to-end-dw.md
│       ├── 0019-tratamiento-registros-invalidos.md
│       ├── 0020-plataforma-bi.md
│       ├── 0021-refresh-bronze-full-reload.md
│       ├── 0022-validacion-schema-ingesta.md
│       ├── 0023-ui-dagster-containerizada.md
│       ├── 0024-motor-transformacion-y-dw.md
│       └── 0025-testing-pipeline-datos.md
│
├── infra/
│   ├── Dockerfile                  # Imagen del servicio API
│   ├── docker-compose.yml          # API + Prometheus + Grafana + Alertmanager + cAdvisor (+ perfiles bi/orchestration)
│   ├── Dockerfile.dagster          # Imagen de la UI de Dagster (perfil orchestration)
│   └── datahub/
│       └── dbt_recipe.yml          # Receta de ingesta DataHub (linaje desde artefactos dbt)
│
├── monitoring/
│   ├── prometheus.yml              # Scraping de métricas
│   ├── alerts.yml                  # Reglas de alerta de Prometheus
│   ├── alertmanager.yml            # Routing de alertas a Slack
│   └── grafana/
│       ├── provisioning/           # Datasources (Prometheus, CloudWatch) y proveedor de dashboards
│       └── dashboards/
│           └── api-metrics.json.tpl  # Template del dashboard (resuelto por grafana-init)
│
├── CONTRIBUTING.md                 # Guía de contribución y workflow de PRs
├── .gitignore
└── README.md
```

---

## Configuración

Antes de levantar el sistema (con o sin Docker), crear el archivo `api/.env` a partir del ejemplo:

```bash
cp api/.env.example api/.env
```

Y completar las variables requeridas:

| Variable | Descripción |
|---|---|
| `API_KEY` | Clave estática que valida el header `X-API-Key` en cada request. **Usar el valor preconfigurado especificado en la consigna de la Fase 1.** |
| `RATE_LIMIT` | Límite de requests por IP (formato SlowAPI, ej. `60/minute`). |

> El `.env` está ignorado por git. La clave nunca se commitea al repositorio.

---

## Levantar el sistema localmente

> Las URLs de esta sección apuntan al **host local** del desarrollador (puertos publicados por Docker o por Uvicorn). La URL pública del servicio desplegado se documenta en [Servicio desplegado](#servicio-desplegado).

### Con Docker (recomendado)

Requiere tener [Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado y el `api/.env` creado (ver [Configuración](#configuración)).

```bash
docker compose -f infra/docker-compose.yml up
```

Servicios expuestos en el host:

| Servicio | Puerto | Ruta |
|---|---|---|
| API REST | 8000 | `/` |
| Documentación Swagger | 8000 | `/docs` |
| Grafana | 3000 | `/` |
| Prometheus | 9090 | `/` |
| Alertmanager | 9093 | `/` |
| cAdvisor | 8080 | `/` |
| Metabase (BI) | 3001 | `/` — perfil `bi`, ver abajo |
| Dagster UI | 3070 | `/` — perfil `orchestration`, ver abajo |

Metabase requiere el perfil `bi` y que exista la base `metabase_app` en el DW. Para
levantarlo en local (apuntando al Postgres del perfil `local-db`):

```bash
docker compose -f infra/docker-compose.yml --profile bi --profile local-db up
```

En staging/producción ya corre en el host; acceder en el puerto 3001 (ver sección
[Acceso a BI y gobierno de datos](#acceso-a-bi-y-gobierno-de-datos)).

### Acceso a Grafana

- **Visor externo (solo lectura):** `ext_read` / `visitor123`. Se provisiona automáticamente al arrancar el stack mediante el init container `grafana-user-init`.
- **Link kiosko para operarios:** ruta `/d/verified-infra-dash?kiosk=true` sobre el host de Grafana — oculta la barra de navegación y bloquea edición.
- **Auto-detección de instancia EC2:** el dashboard es un template (`api-metrics.json.tpl`); un init container (`grafana-init`) consulta IMDSv2 al arrancar y resuelve el `instance-id` del host. La misma imagen corre en staging y producción sin reconfiguración.

### Sin Docker (desarrollo local)

Requiere Python 3.11+ y el `api/.env` creado (ver [Configuración](#configuración)).

```bash
cd api

# Crear y activar entorno virtual
python -m venv venv
source venv/bin/activate       # Linux/Mac
# venv\Scripts\activate        # Windows

# Instalar dependencias
pip install -r requirements.txt

# Ejecutar servidor
uvicorn app.main:app --reload
```

La API queda disponible en el puerto `8000` del host (`/docs` para Swagger).

---

## Tests

Desde la raíz del repositorio, instalar dependencias de desarrollo y correr la suite de pytest:

```bash
pip install -r api/requirements-dev.txt
API_KEY=test-key pytest api/tests/ -v
```

El análisis estático (mismo que corre el CI) se ejecuta con:

```bash
ruff check api/app/
```

---

## Workflows del pipeline de datos

La ingesta de datos se orquesta con **Dagster**. Trae las dos fuentes de
datos.gob.ar a la **capa Bronze** y, de ahí, el grafo continúa al DW (carga a
Postgres + dbt). Antes de los comandos conviene entender **cómo fluyen los datos** y
**cómo se dispara el pipeline**.

### Cómo fluye la ingesta: landing → Bronze

La fuente de producción **solo publica el archivo completo** (~144 MB con todo el
histórico desde 2006); no hay forma de pedir "solo un mes". Para no re-descargar ese
archivo cada vez que se reprocesa un período, la extracción se parte en dos pasos:

```
datos.gob.ar  ──(1 descarga)──►  LANDING                 ──(filtra por mes)──►  BRONZE
(CSV completo)                   data/landing/...                              data/bronze/produccion/
                                 produccion.parquet                            anio=YYYY/mes=MM/produccion.parquet
                                 (crudo entero, 1 sola vez)                    (1 parquet por mes, particionado)
```

- **Landing** = una copia cruda del archivo completo, descargada **una sola vez** por
  corrida. Es la zona de aterrizaje desde la cual se derivan las particiones.
- **Bronze** = el mismo dato **particionado por mes** (`anio=YYYY/mes=MM/`). Cada
  partición se obtiene **leyendo del landing y filtrando ese mes**, sin volver a la red.

Así, reprocesar marzo-2024 reescribe solo `anio=2024/mes=3/` leyendo del landing ya
bajado, sin re-descargar 144 MB ni tocar los demás meses. El catálogo de pozos
(`bronze_pozos`) es chico y se baja entero en cada corrida (full refresh). Detalle y
alternativas descartadas en [ADR-012](docs/adr/0012-tipo-de-carga.md) y
[ADR-013](docs/adr/0013-diseno-capa-bronze.md).

| Asset | Qué hace | Salida |
|---|---|---|
| `produccion_raw` | Descarga el CSV completo de producción **a landing** (1 vez) | `data/landing/produccion/produccion.parquet` |
| `bronze_produccion` | **Particionado por mes**: filtra el mes desde el landing | `data/bronze/produccion/anio=YYYY/mes=MM/` |
| `bronze_pozos` | Descarga el catálogo de pozos (full refresh) | `data/bronze/pozos/ingesta=AAAA-MM-DD/` |

### Cómo se dispara el pipeline: automático vs. manual

Hay **dos formas** de correr la ingesta, y resuelven cosas distintas:

#### 1. Automático — cron mensual (es el disparador de producción)

Un **cron del SO** (`0 3 5 * *`, el día 5 de cada mes) ejecuta
`data_pipeline/orchestration/run_pipeline.sh` en cada EC2, *headless* (sin UI). Ese
script hace un **full reload**: en cada corrida

1. descarga el landing (`produccion_raw`) y el catálogo (`bronze_pozos`);
2. **reescribe TODAS las particiones mensuales** de Bronze desde el landing (no solo el
   último mes), para capturar correcciones que la fuente publica sobre meses viejos
   (columna `rectificado`, ver [ADR-021](docs/adr/0021-refresh-bronze-full-reload.md));
3. ejecuta el job `dw_publish`: carga Bronze→Postgres y corre dbt (Silver/Gold + Data
   Quality). Si un check `error` falla, **se frena la promoción a Gold**.

```bash
# Lo que corre el cron (también sirve para forzar un refresh completo a mano):
bash data_pipeline/orchestration/run_pipeline.sh
```

La cadencia es mensual porque la fuente publica ~una vez por mes. No usa el scheduler de
Dagster: la cadencia la pone el cron del SO (ver [ADR-018](docs/adr/0018-orquestacion-end-to-end-dw.md)).

#### 2. Manual — backfill dirigido de un mes (fuera de ciclo)

Para forzar **un período puntual** sin esperar al cron ni recargar todo (p. ej. la fuente
corrigió un mes y hay que propagarlo ya), se re-materializa **solo esa partición**,
leyendo del landing ya descargado:

```bash
MOD=data_pipeline.orchestration.definitions

# Un mes (la partición usa el formato AAAA-MM-01)
dagster asset materialize --select bronze_produccion --partition "2024-03-01" -m $MOD

# Un rango de meses
dagster asset materialize --select bronze_produccion \
  --partition-range 2024-01-01...2024-03-01 -m $MOD
```

También se lanza desde la UI con el botón **Backfill** del asset. El procedimiento
completo (disparador, validación, rollback) está en el
[runbook del Data Engineer](docs/runbooks/data-engineer.md).

> **En resumen:** el **automático** (cron → full reload de todos los meses) mantiene el DW
> al día sin intervención; el **manual** (backfill de una partición) es la vía rápida para
> reprocesar un mes corregido fuera de ciclo. Ambos derivan de Bronze del **mismo landing**.

### Requisitos

```bash
pip install -r data_pipeline/requirements.txt
```

### Levantar la UI de Dagster (logs y status)

La UI muestra el grafo de assets, los logs y el status de cada corrida, y permite
materializar/backfillear desde el navegador. En producción **no** es el disparador (eso es
el cron); sirve para observabilidad y corridas on-demand.

**Opción A — con el venv local (desarrollo):**

```bash
dagster dev -m data_pipeline.orchestration.definitions
```

La UI queda en **http://localhost:3000**.

**Opción B — containerizada (perfil `orchestration`):** el compose trae un servicio
`dagster` (webserver + daemon) con todo preinstalado. Combinar con `local-db` para
tener el DW al lado:

```bash
docker compose -f infra/docker-compose.yml --profile orchestration --profile local-db up
```

La UI queda en **http://localhost:3070** (3000 lo usa Grafana). El servicio no arranca
con un `up` por defecto ni entra en el build de CI; se levanta donde haya capacidad
(ver [ADR-023](docs/adr/0023-ui-dagster-containerizada.md)).

### Actualizar / agregar workflows

La lógica de extracción vive en `data_pipeline/extraction/` y los assets en
`data_pipeline/orchestration/assets.py`. Tras editar, validar que cargan:

```bash
dagster definitions validate -m data_pipeline.orchestration.definitions
```

### Tests del pipeline

```bash
pip install -r data_pipeline/requirements-dev.txt
pytest data_pipeline/tests/
```

### Troubleshooting

- **Falla la descarga (fuente caída/lenta):** los assets reintentan con backoff
  exponencial (3 intentos). La extracción es idempotente, así que re-materializar
  es seguro. El detalle del error queda en los logs de la corrida en la UI.
- **Una partición quedó vacía:** las particiones mensuales cubren todo el rango
  desde 2006; un mes sin datos en la fuente genera un parquet de 0 filas (no es un
  error).
- **Cambió un mes histórico:** ver *Backfill* arriba; reescribe solo ese mes.

---

## Arquitectura de datos

El sistema implementa una **arquitectura Medallion** de tres capas sobre PostgreSQL 16
(Amazon RDS), orquestada por Dagster y transformada con dbt. Los datos provienen de
dos fuentes del Ministerio de Energía de Argentina (datos.gob.ar).

```
datos.gob.ar  →  Bronze (parquet)  →  bronze.* (Postgres)  →  silver.*  →  gold.* + dq.*
                  Data Engineer (A)        Analytics Engineer (B) — cron mensual en EC2
```

| Capa | Esquema | Descripción | Inmutable |
|---|---|---|---|
| **Bronze** | `data/bronze/` (parquet) + `bronze.*` | Crudo de la fuente tal como llega: todo como texto, sin transformar. Particionado por `anio/mes` para producción. | Sí |
| **Silver** | `silver.*` | Limpio y tipado. Filas con errores duros van a cuarentena (`dq.silver_produccion_rechazos`), no se descartan en silencio. | No (full refresh) |
| **Gold** | `gold.*` | Modelo estrella listo para BI y la API. Grano `(pozo, mes)`. Surrogate keys `sk_*` en todas las dimensiones. | No (full refresh) |
| **Semantic** | `semantic.*` | Vistas semánticas pre-unificadas sobre Gold. Exponen métricas y dimensiones en lenguaje de negocio sin surrogate keys; contrato estable para BI. Ver [ADR-027](docs/adr/0027-semantic-layer.md). | No (view on Gold) |
| **Data Quality** | `dq.*` | Resultados de los 31 checks dbt por corrida (`dq_results`), cuarentena y `store_failures`. Un check `severity: error` bloquea la promoción a Gold. | Acumulativo |

### Modelo estrella (Gold)

La fact table `gold.fact_produccion_mensual` tiene grano `(idpozo, anio, mes)` y
cuatro dimensiones conformadas: `dim_pozo`, `dim_operadora`, `dim_yacimiento` y
`dim_fecha`. Surrogate keys enteras `sk_*` en todas las dimensiones. SCD Type 1 en
`dim_pozo` y `dim_operadora` (la historia de la relación pozo↔operadora queda en la
fact, no en la dimensión). Detalle completo en [docs/data-model.md](docs/data-model.md).

Medidas aditivas: `prod_pet`, `prod_gas`, `prod_agua`, `iny_*`. `tef` (tiempo efectivo
de producción) es semi-aditiva: se promedia, no se suma.

### Frescura

El pipeline corre mensualmente por cron (`0 3 5 * *`) en cada EC2. Los datos en
`gold.*` tienen una **latencia máxima de ~1 mes** respecto de la fuente. Un refresh
manual se dispara con:

```bash
bash data_pipeline/orchestration/run_pipeline.sh
```

Cada corrida hace **full reload** de todo Bronze (todas las particiones mensuales),
para capturar correcciones de la fuente de cualquier antigüedad (ver
[ADR-021](docs/adr/0021-refresh-bronze-full-reload.md)). Para forzar un mes puntual
fuera de ciclo sin recargar todo, usar el backfill por partición (ver *Backfill* arriba
y el [runbook del Data Engineer](docs/runbooks/data-engineer.md)).

---

## Endpoints principales

| Método | Endpoint | Descripción | Auth |
|---|---|---|---|
| GET | `/health` | Health check del servicio | No |
| GET | `/api/v1/wells` | Listado de pozos disponibles | Sí |
| GET | `/api/v1/forecast` | Pronóstico de producción de un pozo | Sí |

Documentación interactiva disponible en `/docs` (Swagger UI) y `/redoc` (ReDoc) con el servicio corriendo. Detalle de parámetros y códigos de respuesta en [api/README.md](api/README.md).

---

## Autenticación

Todos los endpoints bajo `/api/v1/` requieren una API key estática enviada por header:

```
X-API-Key: <API_KEY>
```

Donde `<API_KEY>` es el valor preconfigurado definido en la consigna de la Fase 1 (el mismo que se setea en `api/.env` del lado del servidor).

Ejemplo de request:

```bash
curl -H "X-API-Key: <API_KEY>" \
  "<URL_DEL_SERVICIO>/api/v1/wells?date_query=2024-01-01"
```

Si el header está ausente o no coincide con el valor configurado, la API responde **HTTP 403 Forbidden**.

---

## Acceso a BI y gobierno de datos

### Metabase (plataforma de BI)

Metabase lee preferentemente del esquema `semantic.*` (vistas semánticas pre-unificadas sobre Gold) y del esquema `gold.*` para consultas avanzadas. Está desplegado en la EC2 de producción.

| Ambiente | URL |
|---|---|
| Producción | http://18.117.126.59:3001 |

Solicitar credenciales de Viewer al administrador del sistema. El procedimiento de
exploración de dashboards está en el [runbook de usuario de BI](docs/runbooks/bi-user.md).

**Dashboards disponibles:** Producción No Convencional — producción mensual por
yacimiento (top 5 + otros desde 2015), tendencia de petróleo y gas (gráfico combo),
top 8 pozos por producción histórica total, y KPIs de frescura y calidad del pipeline.

**Cuidados al construir preguntas propias:** las medidas aditivas son `prod_pet`,
`prod_gas`, `prod_agua`, `iny_*`; `tef` se promedia, no se suma. El eje temporal es
`dim_fecha.periodo` (`AAAA-MM`). Para consultas sin conocimiento del modelo estrella,
usar las vistas del esquema `semantic.*` (ej. `sem_produccion_mensual_por_yacimiento`,
`sem_top_pozos`) que exponen métricas ya unificadas en lenguaje de negocio. Para
consultas avanzadas que requieran joins cruzados, usar `gold.*` directamente con
surrogate keys `sk_*`.

### DataHub (gobierno de datos)

DataHub ingiere el manifiesto dbt (`transform/target/manifest.json`) generado en
cada corrida del pipeline y expone el linaje Bronze→Silver→Gold a nivel tabla y
columna. Corre en una **EC2 dedicada** (requiere ≥ 4 GB RAM). La ingesta es un job
one-shot que se ejecuta desde la EC2 del pipeline tras cada `dbt build`:

```bash
export DATAHUB_GMS_HOST=<ip-ec2-gobierno>
datahub ingest -c infra/datahub/dbt_recipe.yml
```

La UI de gobierno queda en `http://<ip-ec2-gobierno>:9002` (usuario `datahub` /
contraseña `datahub`). La IP pública de la EC2 de gobierno cambia en cada
stop/start; consultarla en AWS Console → EC2 → instancia `governance` →
"Public IPv4 address". La IP privada (`172.31.23.108`) es estable y es la que
usa `DATAHUB_GMS_HOST` en `infra/.env` para la ingesta automática del pipeline.

El procedimiento completo de despliegue e ingesta está en el
[runbook del administrador de gobierno](docs/runbooks/governance-admin.md).

---

## Workflow de desarrollo

El proyecto usa **GitFlow** con ramas `main` (versión estable / entregables), `staging` (integración continua) y ramas de trabajo con prefijo `feature/`, `fix/` o `docs/` según el tipo de cambio.

```bash
git checkout staging
git pull
git checkout -b feature/nombre-feature
```

Una vez terminada la rama, abrir un PR hacia `staging`. Otro integrante debe revisar y aprobar antes del merge. Los detalles de comandos comunes, restricciones y convenciones están en [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Tecnologías

**Backend / API**
- **Python 3.11 / FastAPI / Uvicorn** — framework web y servidor ASGI
- **Pydantic** — validación y serialización de schemas
- **SlowAPI** — rate limiting por IP

**Pipeline de datos**
- **Dagster** — orquestador: assets, particiones mensuales y retries con backoff
- **pandas / pyarrow** — lectura de los CSV y escritura de la capa Bronze en parquet
- **requests** — descarga de las fuentes de datos.gob.ar

**Infraestructura**
- **Docker / Docker Compose** — contenerización y orquestación local
- **AWS ECR** — registro privado de imágenes
- **AWS EC2 + Systems Manager (SSM)** — hosts de staging/producción y despliegue sin SSH
- **GitHub OIDC** — autenticación contra AWS sin claves estáticas

**CI/CD**
- **GitHub Actions** — pipeline de test, build y deploy
- **Pytest** — suite de tests unitarios y de integración
- **Ruff** — análisis estático de código
- **Trivy** — escaneo de vulnerabilidades de imágenes Docker

**Monitoreo**
- **Prometheus** — recolección de métricas (`/metrics` instrumentado por `prometheus-fastapi-instrumentator`)
- **Grafana** — dashboards de latencia, disponibilidad y uso de recursos
- **Alertmanager** — routing de alertas a Slack
- **cAdvisor** — métricas de contenedores

---

## Despliegue Continuo (CD) y AWS

El proyecto cuenta con despliegue automatizado hacia instancias **AWS EC2** (staging y producción).

El flujo funciona de la siguiente manera:
1. Al mergear un Pull Request hacia `staging` (dev) o `main` (prod), GitHub Actions dispara el pipeline definido en `.github/workflows/ci.yml`.
2. Se ejecutan los tests (`pytest`), el análisis estático (`ruff`) y se construye la imagen Docker (con escaneo de vulnerabilidades de Trivy).
3. La imagen se publica en **Amazon ECR** (registro privado), autenticándose con GitHub mediante **OIDC** — sin claves estáticas ni `.pem`.
4. El job de deploy usa **AWS Systems Manager (SSM)** para enviar el comando de actualización a la instancia EC2 identificada por tag (`Name=api` para prod, `Name=api-dev` para staging), sin abrir puertos SSH.
5. En la EC2, el script de deploy implementa rollback automático: captura el digest de la imagen actual, pullea la nueva, verifica `/health` con hasta 6 reintentos y si falla restaura la versión anterior.

### Autenticación de la EC2 hacia GitHub

El `git pull` que corre en cada deploy autentica mediante **deploy keys SSH** — una clave ed25519 por instancia, registrada como read-only en el repositorio (Settings → Deploy keys). Las claves privadas viven en `/root/.ssh/github_deploy` de cada EC2. Esto es independiente del punto anterior: la EC2 sigue siendo gestionada por SSM sin exponer el puerto 22; el protocolo SSH aquí refiere únicamente a la autenticación del cliente git contra GitHub. No se usan Personal Access Tokens: no vencen, no tienen alcance de cuenta, y no se filtran en logs de SSM.

Detalles completos en [ADR-002](docs/adr/0002-docker-containerizacion.md).

---

## Architecture Decision Records (ADRs)

Cada decisión de diseño relevante de esta fase está documentada en `docs/adr/`. El formato sigue el modelo simple: contexto → alternativas → decisión → consecuencias.

| # | Título | Resumen |
|---|---|---|
| [001](docs/adr/0001-framework-backend.md) | Elección del framework backend | Por qué se eligió FastAPI y Uvicorn para el backend de la API REST |
| [002](docs/adr/0002-docker-containerizacion.md) | Uso de Docker para contenerización | Pipeline CI/CD completo, ECR + EC2 + SSM y rollback automático |
| [003](docs/adr/0003-prometheus-grafana-monitoreo.md) | Prometheus + Grafana para monitoreo | Recolección de métricas y dashboards on-premise vs SaaS |
| [004](docs/adr/0004-alertmanager-slack-notificaciones.md) | Notificaciones de incidentes con Alertmanager y Slack | Routing de alertas declarativas hacia el canal de incidentes |
| [005](docs/adr/0005-cloudwatch-monitoreo-ec2.md) | CloudWatch como datasource de Grafana | Cómo se integran las métricas del host EC2 al dashboard |
| [006](docs/adr/0006-limpieza-disco-ec2.md) | Estrategia de limpieza de disco en EC2 | Política de poda de imágenes Docker tras incidente del 20-abr-2026 |
| [007](docs/adr/0007-rate-limiting-api.md) | Rate limiting en la API | Por qué SlowAPI por IP y configuración del límite |
| [008](docs/adr/0008-operational-endpoints.md) | Endpoints operativos `/health` y `/mock-500` | Para qué sirven, por qué quedan fuera de la API key |
| [009](docs/adr/0009-testing-strategy-api.md) | Estrategia de unit testing de la API | Alcance de los tests, fixtures y patching de la API key |
| [010](docs/adr/0010-api-key-validation-strategy.md) | Estrategia de validación de API Key | Por qué la validación corre como middleware ASGI (fail-fast on auth) |
| [011](docs/adr/0011-orquestador.md) | Elección de la herramienta de orquestación | Airflow vs Prefect vs Dagster — por qué Dagster para la ingesta de datos |
| [012](docs/adr/0012-tipo-de-carga.md) | Tipo de carga a la capa Bronze | Full refresh vs incremental vs merge — justificado por dataset |
| [013](docs/adr/0013-diseno-capa-bronze.md) | Diseño de la capa Bronze | Formato parquet, todo como texto, particionado por anio/mes y landing |
| [014](docs/adr/0014-arquitectura-medallion.md) | Arquitectura Medallion del DW | Bronze → Silver → Gold: separación de responsabilidades y contratos entre capas |
| [015](docs/adr/0015-modelo-dimensional-estrella.md) | Modelo dimensional del DW (estrella) | Estrella vs snowflake vs OBT; grano de la fact, dims conformadas y decisión de SCD |
| [016](docs/adr/0016-estrategia-data-quality.md) | Estrategia de Data Quality | Gate de calidad entre Silver y Gold; 31 checks con 5 dimensiones y cuarentena |
| [017](docs/adr/0017-plataforma-gobierno-datos.md) | Plataforma de gobierno de datos | DataHub vs OpenMetadata/Amundsen/Marquez; linaje tabla/columna desde artefactos dbt |
| [018](docs/adr/0018-orquestacion-end-to-end-dw.md) | Orquestación end-to-end del DW | dagster-dbt + cron headless; flujo Bronze→Postgres→Silver/Gold/DQ automatizado |
| [019](docs/adr/0019-tratamiento-registros-invalidos.md) | Tratamiento de registros inválidos | Cuarentena vs rechazo vs corrección; filas que violan validaciones duras |
| [020](docs/adr/0020-plataforma-bi.md) | Plataforma de BI | Metabase vs Superset vs Redash; por qué Metabase para usuarios no técnicos |
| [021](docs/adr/0021-refresh-bronze-full-reload.md) | Estrategia de refresh de Bronze | Full reload vs ventana incremental en el refresh recurrente de Bronze |
| [022](docs/adr/0022-validacion-schema-ingesta.md) | Validación de schema en la ingesta | Contrato de columnas por fuente; fail-fast ante cambios de schema |
| [023](docs/adr/0023-ui-dagster-containerizada.md) | UI de Dagster containerizada | Complementa ADR-018: UI vía perfil de compose; `dagster dev` y por qué no toca el cron de prod |
| [024](docs/adr/0024-motor-transformacion-y-dw.md) | Motor de transformación y del DW | Por qué dbt (vs SQLMesh/Dataform/Pandas/Spark) y PostgreSQL/RDS (vs DuckDB/MPP) |
| [025](docs/adr/0025-testing-pipeline-datos.md) | Testing del pipeline de datos | Tests de extracción y DAGs con I/O mockeado (`materialize()`); contratos de idempotencia/particiones/fail-fast |
| [026](docs/adr/0026-restart-policy-datahub-ec2.md) | Política de reinicio de DataHub en EC2 | `unless-stopped` en los 6 contenedores de larga duración; script idempotente de setup |
| [027](docs/adr/0027-semantic-layer.md) | Capa semántica sobre Gold | Vistas SQL en esquema `semantic.*` vs. dbt MetricFlow vs. Cube.dev; abstracción del modelo estrella para BI |
