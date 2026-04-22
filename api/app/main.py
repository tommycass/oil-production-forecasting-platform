from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.routes import health, wells, forecast
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi.errors import RateLimitExceeded
from app.core.rate_limit import limiter

tags_metadata = [
    {
        "name": "Pozos",
        "description": "Consulta de pozos petroleros activos a una fecha determinada.",
    },
    {
        "name": "Forecast",
        "description": "Pronóstico de producción diaria por pozo para un rango de fechas.",
    },
    {
        "name": "Health",
        "description": "Verificación del estado del servicio.",
    },
]

app = FastAPI(
    title="Oil & Gas Forecast API",
    description="""
## Descripción

API REST para consultar pozos petroleros activos y sus pronósticos de producción.

## Autenticación

Todos los endpoints (excepto `/health`) requieren una API key en el header:

```
X-API-Key: <tu-api-key>
```

## Pozos disponibles (mock)

| ID | Producción base |
|----|----------------|
| POZO-001 | 200 m³/día |
| POZO-002 | 150 m³/día |
| POZO-003 | 100 m³/día |
""",
    version="1.0.0",
    openapi_tags=tags_metadata,
    contact={
        "name": "Equipo Oil & Gas Forecast",
    },
)

app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Return a 429 JSON response when a client exceeds the configured rate limit."""
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Try again later."},
    )


app.include_router(health.router)
app.include_router(wells.router)
app.include_router(forecast.router)

Instrumentator(excluded_handlers=["/metrics"]).instrument(app).expose(app)
