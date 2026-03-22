from fastapi import APIRouter

router = APIRouter()


@router.get(
    "/health",
    tags=["Health"],
    summary="Health check",
    description="Verifica que el servicio esté funcionando correctamente.",
    responses={200: {"content": {"application/json": {"example": {"status": "ok"}}}}},
)
def health_check():
    return {"status": "ok"}
