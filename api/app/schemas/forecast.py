from pydantic import BaseModel
from datetime import date


class ForecastPoint(BaseModel):
    date: date
    prod: float


class ForecastResponse(BaseModel):
    id_well: str
    data: list[ForecastPoint]
