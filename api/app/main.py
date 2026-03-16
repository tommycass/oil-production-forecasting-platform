from fastapi import FastAPI
from app.routes import health

app = FastAPI(
    title="Oil & Gas Forecast API",
    description="API para consultar el listado de pozos y sus pronósticos de producción.",
    version="1.0.0",
)

app.include_router(health.router)
