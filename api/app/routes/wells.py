from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from app.schemas.wells import WellResponse
from app.core.security import verify_api_key
from app.services.wells import get_wells

router = APIRouter()


@router.get(
    "/api/v1/wells",
    tags=["Pozos"],
    response_model=list[WellResponse],
    summary="Listar pozos activos",
    description="Retorna el listado de pozos activos a la fecha indicada. La fecha no puede ser futura.",
    responses={
        403: {"description": "API key inválida o ausente"},
        422: {"description": "La fecha es futura o tiene formato inválido"},
    },
)
def wells(date_query: date, api_key: None = Depends(verify_api_key)):
    if date_query > date.today():
        raise HTTPException(status_code=422, detail="date_query cannot be a future date")
    return get_wells(date_query)
