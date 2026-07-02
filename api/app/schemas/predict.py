from typing import Literal

from pydantic import BaseModel, Field

# Targets soportados (ADR-042): petróleo y gas. Coincide con ml.config.TARGETS.
# Pydantic valida el valor (un target inválido devuelve 422 automáticamente).
Target = Literal["prod_pet", "prod_gas"]


class PredictRequest(BaseModel):
    idpozo: int = Field(..., description="ID numérico del pozo")
    anio: int = Field(..., ge=2000, le=2099, description="Año del mes a predecir (t+1)")
    mes: int = Field(..., ge=1, le=12, description="Mes a predecir (1-12, t+1)")
    target: Target = Field(
        default="prod_pet",
        description="Qué producción predecir: prod_pet (petróleo) o prod_gas (gas). "
        "Por defecto petróleo.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {"idpozo": 507, "anio": 2026, "mes": 6, "target": "prod_pet"}
        }
    }


class PredictResponse(BaseModel):
    idpozo: int
    anio: int
    mes: int
    target: Target = Field(..., description="Producción predicha: prod_pet o prod_gas")
    prediccion: float = Field(..., description="Producción predicha del target (m³)")
    unidad: str = Field(default="m3", description="Unidad de la predicción")
    model_name: str = Field(..., description="Modelo registrado en MLflow que sirvió la predicción")
    model_version: str = Field(..., description="Versión del modelo en el MLflow registry")
    model_stage: str = Field(default="Production", description="Stage del modelo en MLflow")

    model_config = {
        "json_schema_extra": {
            "example": {
                "idpozo": 507,
                "anio": 2026,
                "mes": 6,
                "target": "prod_pet",
                "prediccion": 423.7,
                "unidad": "m3",
                "model_name": "produccion-forecast",
                "model_version": "3",
                "model_stage": "Production",
            }
        }
    }
