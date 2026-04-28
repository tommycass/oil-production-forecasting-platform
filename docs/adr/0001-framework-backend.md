# Título: ADR-001: Elección del framework backend
**Estado:** Aceptado

## Contexto
Necesitamos un framework para desarrollar la API REST de la plataforma de pronóstico de producción petrolera. La consigna de la Fase 1 exige cumplir con una especificación OpenAPI estricta (parámetros tipados, formatos de fecha, códigos de respuesta), por lo que el framework debe:

- Ser rápido de desarrollar y de mantener.
- Soportar asincronía nativamente para no bloquear el event loop ante operaciones I/O futuras (consumo de modelos, lecturas de DB).
- Facilitar la validación estricta de tipos de datos en request y response.
- Generar documentación OpenAPI/Swagger sin trabajo manual extra (la consigna pide que la API esté documentada con OpenAPI y accesible en línea).

Se evaluaron **FastAPI** y **Flask** como alternativas, ambas opciones populares y bien soportadas dentro del ecosistema Python.

## Decisión
Usaremos **FastAPI** corriendo sobre **Uvicorn** como servidor ASGI.

### Por qué FastAPI sobre Flask
- **Validación de schemas con Pydantic integrada:** los modelos de request/response se declaran como clases Pydantic (`api/app/schemas/`). FastAPI los usa para parsear y validar automáticamente cada request: si llega un `date_start` con formato inválido o falta `id_well`, el framework devuelve un `422 Unprocessable Entity` con detalle del error sin que tengamos que escribir lógica de validación. En Flask habría que validar a mano o sumar una librería extra (Marshmallow, etc.) y mantener los schemas separados de la lógica de ruteo.
- **OpenAPI/Swagger automático:** FastAPI deriva la spec OpenAPI directamente de los type hints y los schemas Pydantic, exponiéndola en `/docs` (Swagger UI) y `/redoc` sin configuración adicional. Esto cumple con el requerimiento de la consigna de tener documentación accesible en línea, y garantiza que la doc nunca se desincroniza del código. Flask no trae esto out-of-the-box.
- **Asincronía nativa:** FastAPI está construido sobre Starlette y soporta `async/await` de forma natural. Flask es WSGI sincrónico, y aunque hoy soporta vistas `async`, no aprovecha el modelo asincrónico de extremo a extremo.
- **Type hints como contrato:** los handlers usan type hints estándar de Python (`id_well: str`, `date_start: date`), lo que mejora la legibilidad y permite que el editor y mypy detecten errores antes de runtime.

### Por qué Uvicorn como servidor ASGI
- FastAPI es un framework ASGI; necesita un servidor ASGI para correr (no puede usar `gunicorn` por sí solo ni el servidor de desarrollo de Flask).
- Uvicorn es el servidor ASGI **recomendado oficialmente** por la documentación de FastAPI y Starlette, basado en `uvloop` y `httptools` (implementaciones en C de alto rendimiento).
- Es liviano, fácil de configurar (`uvicorn app.main:app`) y se integra sin fricción con Docker (un solo proceso en foreground, sin demonios) — lo que simplifica el `Dockerfile` y el manejo de logs por stdout.
- Soporta hot-reload en desarrollo (`--reload`), útil mientras se itera en endpoints.

## Consecuencias
**Positivas:**
- Desarrollo rápido y con menos errores gracias a la validación de tipos automática vía Pydantic.
- Documentación OpenAPI siempre actualizada y accesible en `/docs` — cumple directamente el requerimiento de la consigna sin trabajo adicional.
- Alto rendimiento en endpoints asíncronos gracias a Starlette + Uvicorn.
- Schemas explícitos y versionados en `api/app/schemas/` que sirven como contrato entre el backend y los consumidores externos.

**Negativas:**
- El ecosistema de plugins/extensiones es más chico que el de Flask.
- Los desarrolladores deben familiarizarse con `async/await` y con el modelo de Pydantic (declarar modelos en lugar de manipular `dict`).
- Dependencia adicional explícita en el servidor (Uvicorn), que en Flask vendría implícita en el `flask run` de desarrollo.
