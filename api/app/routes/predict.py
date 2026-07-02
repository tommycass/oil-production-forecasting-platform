import pandas as pd
from fastapi import APIRouter, HTTPException, Request

from app.core.rate_limit import limiter, RATE_LIMIT
from app.schemas.predict import PredictRequest, PredictResponse
from app.services.feature_reader import get_features_for_inference
from app.services.model_loader import get_loader

router = APIRouter()


@router.post(
    "/api/v1/predict",
    tags=["ML"],
    response_model=PredictResponse,
    summary="Predicción de producción mensual (petróleo o gas)",
    description="""
Predice la producción (m³) de un pozo para el mes solicitado.

- `idpozo`: ID numérico del pozo (obtenible con `/wells`).
- `anio`, `mes`: el **mes a predecir** (t+1). La API busca automáticamente las
  features del mes anterior (t) en el feature store.
- `target`: `prod_pet` (petróleo, por defecto) o `prod_gas` (gas). Cada uno usa su
  tabla del feature store y su modelo en el registry (ADR-042).

El modelo se carga desde el MLflow Model Registry (stage `Production`) y se actualiza
automáticamente cuando se promueve una nueva versión, sin reiniciar el servidor.
""",
    responses={
        403: {"description": "API key inválida o ausente"},
        404: {"description": "Pozo sin datos de features en el mes base"},
        503: {"description": "Modelo no disponible (MLflow inalcanzable o sin versión Production)"},
        429: {"description": "Rate limit excedido"},
    },
)
@limiter.limit(RATE_LIMIT)
def predict(request: Request, body: PredictRequest):
    """Predice la producción del target (petróleo/gas) del mes (anio, mes) para el pozo."""
    try:
        features = get_features_for_inference(body.idpozo, body.anio, body.mes, body.target)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    loader = get_loader(body.target)
    try:
        # Una fila con las 29 features; el Pipeline del modelo selecciona por nombre
        # (one-hot/imputación incluidos), así que el orden de columnas no importa.
        df = pd.DataFrame([features])
        prediction = loader.predict(df)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return PredictResponse(
        idpozo=body.idpozo,
        anio=body.anio,
        mes=body.mes,
        target=body.target,
        prediccion=round(prediction, 2),
        model_name=loader.model_name,
        model_version=loader.version or "unknown",
    )
