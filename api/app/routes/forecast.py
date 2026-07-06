from datetime import date

from fastapi import APIRouter, HTTPException, Request

from app.core.rate_limit import limiter, RATE_LIMIT
from app.schemas.forecast import ForecastResponse, Target
from app.services.forecast import ForecastRangeError, MAX_FORECAST_MONTHS, get_forecast
from app.services.wells import well_exists_in_dw

router = APIRouter()


@router.get(
    "/api/v1/forecast",
    tags=["Forecast"],
    response_model=ForecastResponse,
    summary="Get production forecast",
    description=(
        "Pronóstico **mensual** de producción de un pozo entre `date_start` y `date_end`: "
        "un punto por mes (fecha = primer día del mes), **solo meses futuros**. Usa el modelo "
        "de ML de forma **recursiva** (ADR-042): predice t+1, realimenta la predicción y sigue. "
        f"El horizonte se acota a {MAX_FORECAST_MONTHS} meses desde el último dato del pozo; si "
        "el rango pedido lo supera, se **recorta** hasta ahí (se devuelven los meses hasta el "
        "tope). Parámetro **opcional** `target`: `prod_pet` (petróleo, por defecto) o `prod_gas`."
    ),
    responses={
        403: {"description": "Invalid or missing API key"},
        404: {"description": "Well does not exist in the DW, or has no history in the feature store"},
        422: {"description": "date_start is after date_end, or the range has no future months to forecast"},
        429: {"description": "Rate limit exceeded"},
        503: {"description": "Model not available (MLflow unreachable or no Production version)"},
    },
)
@limiter.limit(RATE_LIMIT)
def forecast(
    request: Request,
    id_well: str,
    date_start: date,
    date_end: date,
    target: Target = "prod_pet",
):
    """Pronóstico mensual recursivo de un pozo entre date_start y date_end (target opcional)."""
    if date_start > date_end:
        raise HTTPException(status_code=422, detail="date_start must be before date_end")
    if not well_exists_in_dw(id_well):
        raise HTTPException(status_code=404, detail="Well not found")
    try:
        data = get_forecast(id_well, date_start, date_end, target)
    except ForecastRangeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:  # pozo sin serie en el feature store (fuera del universo)
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:  # modelo no disponible en MLflow
        raise HTTPException(status_code=503, detail=str(exc))
    return ForecastResponse(id_well=id_well, data=data)
