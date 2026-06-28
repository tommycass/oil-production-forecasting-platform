from pydantic import BaseModel, Field


class PredictRequest(BaseModel):
    idpozo: int = Field(..., description="ID numérico del pozo")
    anio: int = Field(..., ge=2000, le=2099, description="Año del mes a predecir (t+1)")
    mes: int = Field(..., ge=1, le=12, description="Mes a predecir (1-12, t+1)")

    model_config = {
        "json_schema_extra": {
            "example": {"idpozo": 507, "anio": 2026, "mes": 6}
        }
    }


class PredictResponse(BaseModel):
    idpozo: int
    anio: int
    mes: int
    prod_pet_predicha: float = Field(..., description="Producción de petróleo predicha (m³)")
    model_version: str = Field(..., description="Versión del modelo en el MLflow registry")
    model_stage: str = Field(default="Production", description="Stage del modelo en MLflow")

    model_config = {
        "json_schema_extra": {
            "example": {
                "idpozo": 507,
                "anio": 2026,
                "mes": 6,
                "prod_pet_predicha": 423.7,
                "model_version": "3",
                "model_stage": "Production",
            }
        }
    }
