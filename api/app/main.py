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

## Available wells

Use `GET /api/v1/wells?date_query=YYYY-MM-DD` to retrieve active wells for a date.
Well IDs are numeric strings sourced from the data warehouse (e.g. `507`).
Pass a well ID returned by `/wells` to `/forecast?id_well=<id>`.
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


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Return a clean 500 JSON for any unhandled exception (e.g. DW unreachable)."""
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )


app.include_router(health.router)
app.include_router(wells.router)
app.include_router(forecast.router)
app.include_router(mock_error.router)

Instrumentator(excluded_handlers=["/metrics"]).instrument(app).expose(app)
