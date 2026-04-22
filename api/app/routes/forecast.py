from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Request
from app.schemas.forecast import ForecastResponse
from app.core.security import verify_api_key
from app.services.forecast import get_forecast, WELL_BASE_PRODUCTION
from app.core.rate_limit import limiter, RATE_LIMIT

router = APIRouter()


@router.get(
    "/api/v1/forecast",
    tags=["Forecast"],
    response_model=ForecastResponse,
    summary="Obtener pronóstico de producción",
    description="Retorna el pronóstico de producción diaria de un pozo para el rango de fechas indicado. La fecha de inicio debe ser anterior a la fecha de fin.",
    responses={
        403: {"description": "API key inválida o ausente"},
        404: {"description": "El pozo no existe"},
        422: {"description": "date_start es posterior a date_end o los parámetros tienen formato inválido"},
        429: {"description": "Rate limit excedido"},
    },
)
@limiter.limit(RATE_LIMIT)
def forecast(request: Request, id_well: str, date_start: date, date_end: date, api_key: None = Depends(verify_api_key)):
    if date_start > date_end:
        raise HTTPException(status_code=422, detail="date_start must be before date_end")
    if id_well not in WELL_BASE_PRODUCTION:
        raise HTTPException(status_code=404, detail="Well not found")
    data = get_forecast(id_well, date_start, date_end)
    return ForecastResponse(id_well=id_well, data=data)
