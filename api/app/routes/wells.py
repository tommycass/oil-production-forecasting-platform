from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Request
from app.schemas.wells import WellResponse
from app.core.security import verify_api_key
from app.services.wells import get_wells
from app.core.rate_limit import limiter, RATE_LIMIT

router = APIRouter()


@router.get(
    "/api/v1/wells",
    tags=["Wells"],
    response_model=list[WellResponse],
    summary="List active wells",
    description="Return the list of active wells for the given date. The date cannot be in the future.",
    responses={
        403: {"description": "Invalid or missing API key"},
        422: {"description": "Date is in the future or has an invalid format"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit(RATE_LIMIT)
def wells(request: Request, date_query: date, api_key: None = Depends(verify_api_key)):
    """Return the list of active wells for the given date; rejects future dates with HTTP 422."""
    if date_query > date.today():
        raise HTTPException(status_code=422, detail="date_query cannot be a future date")
    return get_wells(date_query)
