from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Request
from app.schemas.forecast import ForecastResponse
from app.core.security import verify_api_key
from app.services.forecast import get_forecast, WELL_BASE_PRODUCTION
from app.core.rate_limit import limiter

router = APIRouter()


@router.get(
    "/api/v1/forecast",
    tags=["Forecast"],
    response_model=ForecastResponse,
    summary="Obtener pronóstico de producción",
    description="Retorna el pronóstico de producción diaria de un pozo para el rango de fechas indicado. La fecha de inicio debe ser anterior a la fecha de fin.",
    responses={
        403: {"description": "Invalid or missing API key"},
        404: {"description": "Well does not exist"},
        422: {"description": "date_start is after date_end or the parameters have an invalid format"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit("60/minute")
def forecast(request: Request, id_well: str, date_start: date, date_end: date, api_key: None = Depends(verify_api_key)):
    """Return the daily production forecast for a well between date_start and date_end."""
    if date_start > date_end:
        raise HTTPException(status_code=422, detail="date_start must be before date_end")
    if id_well not in WELL_BASE_PRODUCTION:
        raise HTTPException(status_code=404, detail="Well not found")
    data = get_forecast(id_well, date_start, date_end)
    return ForecastResponse(id_well=id_well, data=data)
