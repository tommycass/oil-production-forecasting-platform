# Oil Production Forecasting Platform

Plataforma Predictiva de Producción de Hidrocarburos — Trabajo Integrador de Ingeniería de Software.

El sistema expone una API REST que simula el comportamiento de una plataforma de pronóstico de producción de hidrocarburos, incluyendo infraestructura reproducible con Docker, pipeline de CI/CD y monitoreo con Prometheus y Grafana.

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
├── docs/
│   ├── consigna-fase1.md
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
│       └── 0010-api-key-validation-strategy.md
│
├── infra/
│   ├── Dockerfile                  # Imagen del servicio API
│   └── docker-compose.yml          # API + Prometheus + Grafana + Alertmanager + cAdvisor
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
