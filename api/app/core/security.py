import os
from dotenv import load_dotenv
from fastapi import Header, HTTPException

load_dotenv()

API_KEY = os.getenv("API_KEY")


def verify_api_key(x_api_key: str | None = Header(None)):
    """FastAPI dependency that validates the X-API-Key header against the environment variable."""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
