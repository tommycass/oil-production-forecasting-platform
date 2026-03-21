from fastapi import FastAPI
from app.routes import health, wells, forecast
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(
    title="Oil & Gas Forecast API",
    description="API para consultar el listado de pozos y sus pronósticos de producción.",
    version="1.0.0",
)

app.include_router(health.router)
app.include_router(wells.router)
app.include_router(forecast.router)

Instrumentator(excluded_handlers=["/metrics"]).instrument(app).expose(app)
