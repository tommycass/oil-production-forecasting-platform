from pydantic import BaseModel


class WellResponse(BaseModel):
    id_well: str
