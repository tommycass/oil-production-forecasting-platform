from datetime import date
from fastapi import APIRouter, HTTPException, Request
from app.schemas.forecast import ForecastResponse
from app.services.forecast import get_forecast, MAX_FORECAST_DAYS
from app.services.wells import well_exists_in_dw
from app.core.rate_limit import limiter, RATE_LIMIT

router = APIRouter()


@router.get(
    "/api/v1/forecast",
    tags=["Forecast"],
    response_model=ForecastResponse,
    summary="Get production forecast",
    description=(
        "Return the daily production forecast for a well over the given date range. "
        f"The start date must be before the end date, and the range cannot exceed {MAX_FORECAST_DAYS} days."
    ),
    responses={
        403: {"description": "Invalid or missing API key"},
        404: {"description": "Well does not exist"},
        422: {"description": f"date_start is after date_end, the range exceeds {MAX_FORECAST_DAYS} days, or the parameters have an invalid format"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit(RATE_LIMIT)
def forecast(request: Request, id_well: str, date_start: date, date_end: date):
    """Return the daily production forecast for a well between date_start and date_end."""
    if date_start > date_end:
        raise HTTPException(status_code=422, detail="date_start must be before date_end")
    # Tope de horizonte: sin esto, un rango arbitrariamente largo generaría una respuesta
    # enorme (un paso por día). Se valida antes de tocar el DW (fail-fast).
    horizon_days = (date_end - date_start).days
    if horizon_days > MAX_FORECAST_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"El rango solicitado ({horizon_days} días) supera el máximo de {MAX_FORECAST_DAYS} días",
        )
    if not well_exists_in_dw(id_well):
        raise HTTPException(status_code=404, detail="Well not found")
    data = get_forecast(id_well, date_start, date_end)
    return ForecastResponse(id_well=id_well, data=data)
