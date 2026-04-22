from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get(
    "/mock-500",
    tags=["Health"],
    summary="Mock 500 error",
    description="Endpoint de prueba que siempre devuelve un error 500.",
)
def mock_500():
    raise HTTPException(status_code=500, detail="Internal Server Error (mock)")
