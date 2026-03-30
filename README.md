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
│   └── adr/                        # Architecture Decision Records
│       ├── ADR-001-framework-backend.md
│       ├── ADR-002-docker.md
│       └── ADR-003-monitoring.md
│
├── infra/
│   ├── Dockerfile                  # Imagen del servicio API
│   └── docker-compose.yml          # Orquestación: API + Prometheus + Grafana
│
├── monitoring/
│   ├── prometheus.yml              # Configuración de scraping de métricas
│   └── grafana/
│       └── dashboards/             # Dashboards exportados de Grafana
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

La API requiere el header `X-API-Key: abcdef12345` en todos los requests.

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
develop     → integración continua
feature/*   → una rama por funcionalidad
```

### Crear una feature branch

```bash
git checkout develop
git pull
git checkout -b feature/nombre-feature
```

### Abrir un Pull Request

Una vez terminada la feature, abrir un PR hacia `develop`. Otro integrante debe revisar y aprobar antes del merge.

---

## Tecnologías

- **Python / FastAPI / Uvicorn** — backend y API REST
- **Docker / Docker Compose** — contenerización y orquestación
- **GitHub Actions** — pipeline de CI/CD
- **Prometheus + Grafana** — monitoreo y visualización de métricas

---

## Despliegue Continuo (CD) y AWS

El proyecto cuenta con despliegue automatizado hacia una instancia **AWS EC2**. 

El flujo funciona de la siguiente manera:
1. Al mergear un Pull Request hacia la rama `main`, GitHub Actions dispara el pipeline definido en `.github/workflows/ci.yml`.
2. Se ejecutan los tests, el análisis estático y se construye la imagen Docker.
3. La imagen se publica en GHCR.
4. El job de deploy se conecta vía SSH a la instancia EC2, descarga el código más reciente y reinicia los contenedores utilizando el `docker-compose.yml`.