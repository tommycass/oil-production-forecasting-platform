import os
from dotenv import load_dotenv
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

load_dotenv()

API_KEY = os.getenv("API_KEY")
PROTECTED_PREFIX = "/api/"


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(PROTECTED_PREFIX):
            # Fail-closed: si no hay API_KEY configurada (env/.env ausente), rechazar todo
            # en vez de dejar pasar sin auth (evita que `None != None` sea False y abra la API).
            if API_KEY is None or request.headers.get("x-api-key") != API_KEY:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Forbidden"},
                )
        return await call_next(request)
