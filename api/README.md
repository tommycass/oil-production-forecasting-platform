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
│   └── services/            # Lógica de negocio (mock por ahora)
│       ├── wells.py         # Filtrado de pozos activos por fecha
│       └── forecast.py      # Generación mock de pronósticos
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
| `app/core/demo_data.py` | Catálogo mock con 20 pozos (`POZO-001` a `POZO-020`) más los helpers `get_well` y `well_exists`. |
| `app/services/` | Lógica de negocio separada de los endpoints. Actualmente devuelve datos mock; en fases futuras se reemplazará por el modelo predictivo real. |

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

**GET /api/v1/forecast**
| Parámetro | Tipo | Requerido | Descripción |
|-----------|------|-----------|-------------|
| `id_well` | string | Sí | Identificador del pozo (debe existir en el catálogo, ej: `POZO-001`). |
| `date_start` | fecha (YYYY-MM-DD) | Sí | Fecha de inicio del pronóstico. |
| `date_end` | fecha (YYYY-MM-DD) | Sí | Fecha de fin del pronóstico. Debe ser mayor o igual a `date_start`. |

### Códigos de respuesta

| Código | Descripción |
|--------|-------------|
| 200 | Respuesta exitosa |
| 403 | API key inválida o ausente |
| 404 | Pozo no encontrado |
| 422 | Parámetros inválidos (ej: `date_query` futura, `date_start` mayor a `date_end`, formato incorrecto) |
| 429 | Rate limit excedido |
| 500 | Error interno del servidor (devuelto de forma determinística por `/mock-500` para testing) |
