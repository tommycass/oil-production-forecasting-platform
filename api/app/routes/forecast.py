from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from app.schemas.forecast import ForecastResponse
from app.core.security import verify_api_key
from app.services.forecast import get_forecast, WELL_BASE_PRODUCTION

router = APIRouter()


@router.get("/api/v1/forecast", response_model=ForecastResponse)
def forecast(id_well: str, date_start: date, date_end: date, api_key: None = Depends(verify_api_key)):
    if date_start > date_end:
        raise HTTPException(status_code=422, detail="date_start must be before date_end")
    if id_well not in WELL_BASE_PRODUCTION:
        raise HTTPException(status_code=404, detail="Well not found")
    data = get_forecast(id_well, date_start, date_end)
    return ForecastResponse(id_well=id_well, data=data)
