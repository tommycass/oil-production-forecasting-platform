import pandas as pd
from fastapi import APIRouter, HTTPException, Request

from app.core.rate_limit import limiter, RATE_LIMIT
from app.schemas.predict import PredictRequest, PredictResponse
from app.services.feature_reader import get_features_for_inference, INFERENCE_FEATURES
from app.services.model_loader import MODEL_LOADER

router = APIRouter()


@router.post(
    "/api/v1/predict",
    tags=["ML"],
    response_model=PredictResponse,
    summary="Predicción de producción mensual de petróleo",
    description="""
Predice la producción de petróleo (m³) de un pozo para el mes solicitado.

- `idpozo`: ID numérico del pozo (obtenible con `/wells`).
- `anio`, `mes`: el **mes a predecir** (t+1). La API busca automáticamente las
  features del mes anterior (t) en el feature store.

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
    """Predice la producción de petróleo del mes (anio, mes) para el pozo dado."""
    try:
        features = get_features_for_inference(body.idpozo, body.anio, body.mes)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    try:
        df = pd.DataFrame([features], columns=INFERENCE_FEATURES)
        prediction = MODEL_LOADER.predict(df)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return PredictResponse(
        idpozo=body.idpozo,
        anio=body.anio,
        mes=body.mes,
        prod_pet_predicha=round(prediction, 2),
        model_version=MODEL_LOADER.version or "unknown",
    )
