from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from app.schemas.wells import WellResponse
from app.core.security import verify_api_key
from app.services.wells import get_wells

router = APIRouter()


@router.get("/api/v1/wells", response_model=list[WellResponse])
def wells(date_query: date, api_key: None = Depends(verify_api_key)):
    if date_query > date.today():
        raise HTTPException(status_code=422, detail="date_query no puede ser una fecha futura")
    return get_wells(date_query)