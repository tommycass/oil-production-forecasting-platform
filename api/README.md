# Oil & Gas Forecast API

API REST para consultar el listado de pozos y sus pronósticos de producción de hidrocarburos.

## Stack Tecnológico

- **Framework:** [FastAPI](https://fastapi.tiangolo.com/)
- **Servidor ASGI:** [Uvicorn](https://www.uvicorn.org/)
- **Rate limiting:** [SlowAPI](https://slowapi.readthedocs.io/)
- **Métricas:** [prometheus-fastapi-instrumentator](https://github.com/trallnag/prometheus-fastapi-instrumentator)
- **Lenguaje:** Python 3.11+

## Estructura del Proyecto

```
api/
├── app/
│   ├── __init__.py          # Inicialización del paquete principal
│   ├── main.py              # Entry point: registra routers, middleware, métricas
│   ├── routes/              # Definición de endpoints (routers)
│   │   ├── __init__.py
│   │   ├── health.py        # GET /health — health check del servicio
│   │   ├── wells.py         # GET /api/v1/wells
│   │   ├── forecast.py      # GET /api/v1/forecast
│   │   └── mock_error.py    # GET /mock-500 — endpoint mock que devuelve 500 (testing)
│   ├── schemas/             # Schemas Pydantic (modelos de request/response)
│   │   ├── wells.py         # Schema de respuesta para pozos
│   │   └── forecast.py      # Schemas de respuesta para pronósticos
│   ├── core/                # Lógica central (seguridad, rate limit, datos demo)
│   │   ├── security.py      # Middleware de validación de API key
│   │   ├── rate_limit.py    # Configuración del limiter de slowapi
│   │   └── demo_data.py     # Catálogo mock de pozos y helpers de búsqueda
│   └── services/            # Lógica de negocio + serving ML
│       ├── wells.py         # Filtrado de pozos activos por fecha
│       ├── forecast.py      # Motor del pronóstico recursivo mensual + horizonte
│       ├── model_loader.py  # Carga el modelo del registry MLflow (stage Production)
│       └── feature_reader.py # Lee la serie histórica del feature store (seam Rol 2)
├── tests/                   # Test suite (pytest)
├── .env                     # Variables de entorno (no se sube a GitHub)
├── .env.example             # Ejemplo de variables de entorno requeridas
├── requirements.txt         # Dependencias de runtime
├── requirements-dev.txt     # Dependencias de desarrollo (pytest, ruff, httpx)
├── .gitignore
└── README.md
```

### Descripción de cada módulo

| Módulo | Propósito |
|--------|-----------|
| `app/main.py` | Punto de entrada. Crea la instancia de FastAPI, registra routers, monta el middleware de API key y expone las métricas Prometheus. |
| `app/routes/` | Routers organizados por dominio. Cada archivo define los endpoints de una funcionalidad. |
| `app/schemas/` | Schemas Pydantic para validación de datos de entrada y estructura de respuestas. |
| `app/core/security.py` | `APIKeyMiddleware` que valida el header `X-API-Key` para todas las rutas bajo `/api/`. Devuelve 403 si la clave es inválida o faltante. |
| `app/core/rate_limit.py` | Define el `Limiter` de slowapi y lee el límite desde la variable de entorno `RATE_LIMIT` (por defecto `60/minute`). |
| `app/core/demo_data.py` | Catálogo mock de 20 pozos de prueba (`POZO-001` a `POZO-020`). Remanente de la Fase 1; ya no lo usa `/forecast` (que ahora usa el modelo de ML). |
| `app/services/wells.py` | Consulta el DW real (`gold.dim_pozo`, `gold.fact_produccion_mensual`) para `/wells` y valida la existencia de un pozo para `/forecast` (`well_exists_in_dw`). |
| `app/services/forecast.py` | Orquesta el **pronóstico recursivo mensual** (ADR-044): lee la serie histórica (feature store), corre el motor `ml.forecast`, acota el horizonte y da la respuesta `{date, prod}`. |
| `app/services/model_loader.py` | Carga y sirve el modelo de cada target desde el MLflow registry (stage Production), con recarga automática. |
| `app/services/feature_reader.py` | Contrato de lectura de la serie histórica del feature store para el forecast (implementación a cargo del Rol 2). |

## Requisitos Previos

- Python 3.11 o superior
- pip

## Instalación

1. Clonar el repositorio y navegar a la carpeta de la API:
   ```bash
   cd api
   ```

2. Crear y activar el entorno virtual:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. Instalar las dependencias:
   ```bash
   pip install -r requirements.txt
   ```

   Para correr los tests y el linter, instalar también las dependencias de desarrollo:
   ```bash
   pip install -r requirements-dev.txt
   ```

4. Crear el archivo `.env` en base al ejemplo:
   ```bash
   cp .env.example .env
   ```
   Completar los valores correspondientes en `.env`.

### Variables de entorno

| Variable | Requerida | Default | Descripción |
|----------|-----------|---------|-------------|
| `API_KEY` | Sí | — | Clave que los clientes deben enviar en el header `X-API-Key` para acceder a `/api/`. |
| `RATE_LIMIT` | No | `60/minute` | Límite global de requests por IP (formato slowapi, ej: `100/minute`, `1000/hour`). |

## Ejecución

Levantar el servidor de desarrollo con recarga automática:

```bash
uvicorn app.main:app --reload --port 8000
```

El servidor estará disponible en `http://localhost:8000`.

## Seguridad

Las rutas bajo el prefijo `/api/` están protegidas por `APIKeyMiddleware`. Cada request debe incluir el header:

```
X-API-Key: <tu_api_key>
```

Si la clave es inválida o está ausente, la API devuelve HTTP 403 Forbidden.

Los endpoints `/health`, `/metrics` y `/mock-500` quedan expuestos sin autenticación.

## Rate Limiting

Las rutas `/api/v1/wells` y `/api/v1/forecast` aplican el límite definido por la variable `RATE_LIMIT` (por defecto `60/minute` por IP de origen). Si se supera, la API devuelve:

```json
{ "detail": "Rate limit exceeded. Try again later." }
```

con código HTTP 429.

## Métricas

La API expone métricas en formato Prometheus en `GET /metrics` mediante `prometheus-fastapi-instrumentator`. Incluye contadores y latencias de request por handler. Este endpoint queda excluido de la propia instrumentación.

## Endpoints Disponibles

| Método | Ruta | Descripción | Autenticación | Rate limit |
|--------|------|-------------|---------------|------------|
| GET | `/health` | Health check — devuelve `{"status": "ok"}` | No | No |
| GET | `/metrics` | Métricas Prometheus | No | No |
| GET | `/api/v1/wells` | Listado de pozos activos para una fecha | Sí | Sí |
| GET | `/api/v1/forecast` | Pronóstico de producción de un pozo | Sí | Sí |
| GET | `/mock-500` | Endpoint mock que siempre devuelve HTTP 500 (uso exclusivo para testing) | No | No |

### Parámetros

**GET /api/v1/wells**
| Parámetro | Tipo | Requerido | Descripción |
|-----------|------|-----------|-------------|
| `date_query` | fecha (YYYY-MM-DD) | Sí | Fecha para la cual se consulta el listado. No puede ser una fecha futura. |

**GET /api/v1/forecast** — pronóstico **mensual recursivo** con el modelo de ML (ADR-044): un punto por mes (fecha = 1° del mes), **solo meses futuros**; el horizonte se acota a 12 meses desde el último dato del pozo (si el rango lo supera, se recorta). Respuesta con el contrato de Fase 1: `{id_well, data:[{date, prod}]}`.

| Parámetro | Tipo | Requerido | Descripción |
|-----------|------|-----------|-------------|
| `id_well` | string | Sí | Identificador del pozo. Debe existir en el DW (`gold.dim_pozo`); usar los IDs devueltos por `/api/v1/wells` (valores numéricos, ej: `507`). |
| `date_start` | fecha (YYYY-MM-DD) | Sí | Fecha de inicio del pronóstico. |
| `date_end` | fecha (YYYY-MM-DD) | Sí | Fecha de fin del pronóstico. Debe ser mayor o igual a `date_start`. |
| `target` | string | No | Producción a pronosticar: `prod_pet` (petróleo, por defecto) o `prod_gas` (gas). |

### Códigos de respuesta

| Código | Descripción |
|--------|-------------|
| 200 | Respuesta exitosa |
| 403 | API key inválida o ausente |
| 404 | Pozo no encontrado en el DW, o sin serie histórica en el feature store |
| 422 | Parámetros inválidos (`date_start` mayor a `date_end`, `target` inválido, rango sin meses futuros, o formato incorrecto) |
| 429 | Rate limit excedido |
| 503 | Modelo no disponible en MLflow, o lector de historia del feature store no implementado (`/forecast`) |
| 500 | Error interno del servidor (devuelto de forma determinística por `/mock-500` para testing) |
