# Guía de Desarrollo y Contribución

Este documento proporciona las pautas y comandos necesarios para trabajar con el código de este repositorio.

## Resumen del Proyecto

API REST para el pronóstico de producción de hidrocarburos construida con FastAPI. Actualmente, todos los datos de negocio son simulados — la capa `api/app/services/` genera datos sintéticos, sin conexión a una base de datos real.

## Comandos Comunes

Todos los comandos se ejecutan desde el directorio `api/` a menos que se indique lo contrario.

### Desarrollo Local

```bash
# Instalar dependencias
pip install -r requirements.txt -r requirements-dev.txt

# Levantar la API (requiere la variable de entorno API_KEY)
API_KEY=tu_clave uvicorn app.main:app --reload

# Linter (solo para código de producción — coincide con la configuración de CI)
ruff check app/

# Correr todos los tests
API_KEY=test-key pytest tests/ -v

# Correr un archivo de test específico
API_KEY=test-key pytest tests/test_forecast.py -v

# Correr un test individual
API_KEY=test-key pytest tests/test_forecast.py::test_forecast_valid -v
```

### Docker (desde la raíz del repositorio)

```bash
# Levantar todos los servicios (API + Prometheus + Grafana)
docker compose -f infra/docker-compose.yml up --build

# API: http://localhost:8000
# Prometheus: http://localhost:9090
# Grafana: http://localhost:3000 (admin/admin
```

### Autenticación

Todos los endpoints, excepto `GET /health` y `GET /metrics`, requieren el siguiente header:
```
X-API-Key: <valor de la variable de entorno API_KEY>
```

## Arquitectura

### Estructura de Servicios

```
api/app/
├── main.py          # App factory, registro de routers, configuración de Prometheus
├── core/
│   └── security.py  # Dependencia verify_api_key() de FastAPI
├── routes/          # Routers de health, wells y forecast
├── services/        # Generación de datos mock (lista de pozos, declinación lineal)
└── schemas/         # Modelos de respuesta de Pydantic
```

### Flujo de Request

Petición HTTP → Route handler → Dependencia `verify_api_key` (Chequeo de Header) → Función de Service → Pydantic schema → Respuesta HTTP

### Stack de Monitoreo

- Prometheus scrapea `/metrics` cada 15s; reglas de alerta en `monitoring/alerts.yml`.
- Grafana lee de Prometheus; dashboards provisionados en `monitoring/grafana/`.
- El endpoint `/metrics` está **excluido** del tracking del instrumentador de Prometheus (ADR-003) para evitar inflar artificialmente las métricas de negocio.

### CI/CD

GitHub Actions (`.github/workflows/ci.yml`) ejecuta tres jobs secuenciales:

1. **test** (en push/PR a `staging` o `main`): Ejecuta `ruff check api/app/` y luego `pytest api/tests/ -v` con la API_KEY inyectada.
2. **build** (solo si `test` pasa): Construye la imagen apuntando al contexto correcto (`docker build -f infra/Dockerfile api/`), escanea vulnerabilidades con Trivy, chequea la salud del contenedor y publica las imágenes en GHCR.
3. **deploy** (solo en merge a `main`): Se conecta por SSH a la instancia EC2 en AWS, descarga la última versión del código y despliega utilizando `docker compose -f infra/docker-compose.yml up -d`.

### Git Workflow    

Basado en GitFlow: ramas de features → `staging` → `main`.
Nomenclatura de ramas: `feature/`, `fix/`, `docs/`.

## Restricciones Clave

- La variable de entorno `API_KEY` debe estar configurada tanto para levantar el servicio como para la ejecución de los tests.
- El análisis estático de `ruff` solo se aplica sobre la carpeta `app/` (se excluye `tests/`) — esto refleja exactamente el comportamiento en CI.
- El contexto de construcción del Dockerfile es la carpeta `api/`, pero el archivo se encuentra en la carpeta `infra/`. El comando exacto de compilación debe ser: `docker build -f infra/Dockerfile api/`.

## Consigna y requerimientos

Ver `docs/consigna-fase1.md` para el resumen de entregables y requerimientos de la Fase 1. Los markdowns con la especificación técnica completa y decisiones de diseño se encuentran en el directorio `adr/`.