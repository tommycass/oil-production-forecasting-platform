# Oil & Gas Forecast API

API REST para consultar el listado de pozos y sus pronósticos de producción de hidrocarburos.

## Stack Tecnológico

- **Framework:** [FastAPI](https://fastapi.tiangolo.com/)
- **Servidor ASGI:** [Uvicorn](https://www.uvicorn.org/)
- **Lenguaje:** Python 3.11+

## Estructura del Proyecto

```
api/
├── app/
│   ├── __init__.py          # Inicialización del paquete principal
│   ├── main.py              # Entry point de la aplicación FastAPI
│   ├── routes/              # Definición de endpoints (routers)
│   │   ├── __init__.py
│   │   └── health.py        # GET /health — health check del servicio
│   ├── schemas/             # Schemas Pydantic (modelos de request/response)
│   │   ├── wells.py         # Schema de respuesta para pozos
│   │   └── forecast.py      # Schemas de respuesta para pronósticos
│   ├── core/                # Lógica central (seguridad, configuración)
│   │   └── security.py      # Validación de API key
│   └── middleware/          # Middlewares (vacío por ahora)
├── .env                     # Variables de entorno (no se sube a GitHub)
├── .env.example             # Ejemplo de variables de entorno requeridas
├── requirements.txt         # Dependencias del proyecto
├── .gitignore
└── README.md
```

### Descripción de cada módulo

| Módulo | Propósito |
|--------|-----------|
| `app/main.py` | Punto de entrada. Crea la instancia de FastAPI y registra los routers. |
| `app/routes/` | Contiene los routers organizados por dominio. Cada archivo define los endpoints de una funcionalidad. |
| `app/schemas/` | Define los schemas Pydantic para validación de datos de entrada y estructura de respuestas. |
| `app/core/security.py` | Dependency de FastAPI que valida el header `X-API-Key` en cada request. Devuelve 403 si la clave es inválida o faltante. |
| `app/middleware/` | Middlewares globales (vacío por ahora). |

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

4. Crear el archivo `.env` en base al ejemplo:
   ```bash
   cp .env.example .env
   ```
   Completar los valores correspondientes en `.env`.

## Ejecución

Levantar el servidor de desarrollo con recarga automática:

```bash
uvicorn app.main:app --reload --port 8000
```

El servidor estará disponible en `http://localhost:8000`.

## Seguridad

Todos los endpoints (excepto `/health`) requieren autenticación mediante API key.
La clave debe enviarse en el header de cada request:

```
X-API-Key: <tu_api_key>
```

Si la clave es inválida o está ausente, la API devuelve HTTP 403 Forbidden.

## Endpoints Disponibles

| Método | Ruta | Descripción | Autenticación |
|--------|------|-------------|---------------|
| GET | `/health` | Health check — devuelve `{"status": "ok"}` | No |
| GET | `/api/v1/wells` | Listado de pozos disponibles | Sí |
| GET | `/api/v1/forecast` | Pronóstico de producción de un pozo | Sí |

> Los endpoints `/api/v1/wells` y `/api/v1/forecast` están pendientes de implementación.

## Documentación Interactiva

FastAPI genera documentación automática accesible en:

- **Swagger UI:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc:** [http://localhost:8000/redoc](http://localhost:8000/redoc)
