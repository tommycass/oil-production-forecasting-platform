from datetime import date
from typing import Literal

from pydantic import BaseModel

# Targets soportados (ADR-039): petróleo y gas. Es el valor del parámetro OPCIONAL
# `target` de /forecast (default prod_pet); no forma parte del cuerpo de la respuesta,
# que mantiene el contrato de Fase 1 ({id_well, data:[{date, prod}]}).
Target = Literal["prod_pet", "prod_gas"]


class ForecastPoint(BaseModel):
    date: date
    prod: float

    model_config = {
        "json_schema_extra": {
            "example": {"date": "2024-01-01", "prod": 950.5}
        }
    }


class ForecastResponse(BaseModel):
    id_well: str
    data: list[ForecastPoint]

    model_config = {
        "json_schema_extra": {
            # La salida es MENSUAL (ADR-042): un punto por mes futuro, date = 1° de mes.
            "example": {
                "id_well": "POZO-001",
                "data": [
                    {"date": "2024-01-01", "prod": 950.5},
                    {"date": "2024-02-01", "prod": 940.2},
                    {"date": "2024-03-01", "prod": 930.1},
                ]
            }
        }
    }
