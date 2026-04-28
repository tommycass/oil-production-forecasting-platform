from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.routes import health, wells, forecast, mock_error
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi.errors import RateLimitExceeded
from app.core.rate_limit import limiter
from app.core.security import APIKeyMiddleware

tags_metadata = [
    {
        "name": "Wells",
        "description": "Query active oil wells for a given date.",
    },
    {
        "name": "Forecast",
        "description": "Daily production forecast per well for a date range.",
    },
    {
        "name": "Health",
        "description": "Service health check.",
    },
]

app = FastAPI(
    title="Oil & Gas Forecast API",
    description="""
## Description

REST API to query active oil wells and their production forecasts.

## Authentication

All endpoints (except `/health`) require an API key in the header:

```
X-API-Key: <your-api-key>
```

## Available wells (mock)

| ID | Base production |
|----|----------------|
| POZO-001 | 200 m³/day |
| POZO-002 | 150 m³/day |
| POZO-003 | 100 m³/day |
""",
    version="1.0.0",
    openapi_tags=tags_metadata,
    contact={
        "name": "Oil & Gas Forecast Team",
    },
)

app.state.limiter = limiter
app.add_middleware(APIKeyMiddleware)


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
app.include_router(mock_error.router)

Instrumentator(excluded_handlers=["/metrics"]).instrument(app).expose(app)
