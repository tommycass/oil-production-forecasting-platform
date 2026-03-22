from pydantic import BaseModel
from datetime import date


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
            "example": {
                "id_well": "POZO-001",
                "data": [
                    {"date": "2024-01-01", "prod": 950.5},
                    {"date": "2024-01-02", "prod": 940.2},
                    {"date": "2024-01-03", "prod": 930.1},
                ]
            }
        }
    }
