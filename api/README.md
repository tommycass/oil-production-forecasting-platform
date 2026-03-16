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
│   ├── models/              # Schemas Pydantic (modelos de request/response)
│   │   └── __init__.py
│   └── middleware/           # Middlewares (ej: autenticación por API key)
│       └── __init__.py
├── requirements.txt         # Dependencias del proyecto
├── .gitignore
└── README.md
```

### Descripción de cada módulo

| Módulo | Propósito |
|--------|-----------|
| `app/main.py` | Punto de entrada. Crea la instancia de FastAPI y registra los routers. |
| `app/routes/` | Contiene los routers organizados por dominio. Cada archivo define los endpoints de una funcionalidad. |
| `app/models/` | Define los schemas Pydantic para validación de datos de entrada y estructura de respuestas. |
| `app/middleware/` | Contiene middlewares que se ejecutan en cada request (ej: validación de API key). |

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

## Ejecución

Levantar el servidor de desarrollo con recarga automática:

```bash
uvicorn app.main:app --reload --port 8000
```

El servidor estará disponible en `http://localhost:8000`.

## Endpoints Disponibles

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/health` | Health check — devuelve `{"status": "ok"}` |

## Documentación Interactiva

FastAPI genera documentación automática accesible en:

- **Swagger UI:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc:** [http://localhost:8000/redoc](http://localhost:8000/redoc)
