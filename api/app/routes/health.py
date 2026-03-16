from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health_check():
    """Verifica que el servicio esté funcionando correctamente."""
    return {"status": "ok"}
