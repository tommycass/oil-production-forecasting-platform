from pydantic import BaseModel


class WellResponse(BaseModel):
    id_well: str

    model_config = {
        "json_schema_extra": {
            "example": {"id_well": "POZO-001"}
        }
    }
