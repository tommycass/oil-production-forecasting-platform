import os
from dotenv import load_dotenv
from fastapi import Header, HTTPException

load_dotenv()

API_KEY = os.getenv("API_KEY")


def verify_api_key(x_api_key: str | None = Header(None)):
    """Dependencia FastAPI que valida el header X-API-Key contra la variable de entorno."""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
