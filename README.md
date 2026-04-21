# Oil Production Forecasting Platform

Plataforma Predictiva de Producción de Hidrocarburos — Trabajo Integrador de Ingeniería de Software.

El sistema expone una API REST que simula el comportamiento de una plataforma de pronóstico de producción de hidrocarburos, incluyendo infraestructura reproducible con Docker, pipeline de CI/CD y monitoreo con Prometheus y Grafana.

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
│   │   ├── middleware/             # Interceptores HTTP (ej. autenticación por API key)
│   │   ├── models/                 # Entidades del dominio
│   │   ├── routes/                 # Endpoints de la API
│   │   ├── schemas/                # Estructuras de request/response
│   │   ├── services/               # Lógica de negocio y generación de datos mock
│   │   ├── __init__.py
│   │   └── main.py                 # Punto de entrada de la aplicación FastAPI
│   ├── tests/                      # Tests unitarios y de integración
│   ├── requirements.txt
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
│       └── 0006-limpieza-disco-ec2.md
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
│       └── dashboards/             # Template del dashboard (api-metrics.json.tpl)
│
├── .gitignore
└── README.md
```

---

## Levantar el sistema

### Con Docker (recomendado)

Requiere tener [Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado.

```bash
docker compose -f infra/docker-compose.yml up
```

| Servicio | URL |
|---|---|
| API REST | http://localhost:8000 |
| Documentación Swagger | http://localhost:8000/docs |
| Grafana | http://localhost:3000 |
| Prometheus | http://localhost:9090 |
| Alertmanager | http://localhost:9093 |
| cAdvisor | http://localhost:8080 |

### Acceso a Grafana

- **Administrador:** usuario `admin` / contraseña `admin` (configurable vía `GF_SECURITY_ADMIN_PASSWORD`).
- **Visor externo (solo lectura):** `ext_read` / `visitor123`. Se provisiona automáticamente al arrancar el stack mediante el init container `grafana-user-init`.
- **Link kiosko para operarios:** `http://<host>:3000/d/verified-infra-dash?kiosk=true` — oculta la barra de navegación y bloquea edición.
- **Auto-detección de instancia EC2:** el dashboard es un template (`api-metrics.json.tpl`); un init container (`grafana-init`) consulta IMDSv2 al arrancar y resuelve el `instance-id` del host. La misma imagen corre en staging y producción sin reconfiguración.

### Sin Docker (desarrollo local)

```bash
cd api

# Crear y activar entorno virtual
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux/Mac

# Instalar dependencias
pip install -r requirements.txt

# Ejecutar servidor
uvicorn app.main:app --reload
```

API disponible en http://localhost:8000 — documentación en http://localhost:8000/docs.

---

## Endpoints principales

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/v1/wells` | Listado de pozos disponibles |
| GET | `/api/v1/forecast` | Pronóstico de producción de un pozo |

Ver la [documentación completa en Swagger](http://localhost:8000/docs) con el servicio corriendo.

---

## Workflow de desarrollo

El proyecto usa **GitFlow**.

```
main        → versión estable (entregables)
staging     → integración continua
feature/*   → una rama por funcionalidad
```

### Crear una feature branch

```bash
git checkout staging
git pull
git checkout -b feature/nombre-feature
```

### Abrir un Pull Request

Una vez terminada la feature, abrir un PR hacia `staging`. Otro integrante debe revisar y aprobar antes del merge.

---

## Tecnologías

- **Python / FastAPI / Uvicorn** — backend y API REST
- **Docker / Docker Compose** — contenerización y orquestación
- **GitHub Actions** — pipeline de CI/CD
- **Prometheus + Grafana** — monitoreo y visualización de métricas

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