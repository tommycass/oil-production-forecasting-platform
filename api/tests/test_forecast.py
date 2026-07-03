import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
from app.main import app

client = TestClient(app)

API_KEY = "test-key"
HEADERS = {"X-API-Key": API_KEY}


@pytest.fixture(autouse=True)
def mock_api_key():
    with patch("app.core.security.API_KEY", API_KEY):
        yield


@pytest.fixture(autouse=True)
def mock_well_exists():
    """Evita queries al DW en tests unitarios: well_exists_in_dw retorna True por defecto."""
    with patch("app.routes.forecast.well_exists_in_dw", return_value=True):
        yield


def test_forecast_success():
    response = client.get(
        "/api/v1/forecast",
        params={"id_well": "POZO-001", "date_start": "2024-01-01", "date_end": "2024-01-05"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id_well"] == "POZO-001"
    assert len(data["data"]) == 5


def test_forecast_real_dw_id_accepted():
    """/forecast debe aceptar IDs numéricos reales que devuelve /wells desde el DW."""
    response = client.get(
        "/api/v1/forecast",
        params={"id_well": "12345", "date_start": "2024-01-01", "date_end": "2024-01-03"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id_well"] == "12345"
    assert len(data["data"]) == 3


def test_forecast_well_not_found():
    """Un ID que no existe en el DW debe devolver 404."""
    with patch("app.routes.forecast.well_exists_in_dw", return_value=False):
        response = client.get(
            "/api/v1/forecast",
            params={"id_well": "POZO-999", "date_start": "2024-01-01", "date_end": "2024-01-03"},
            headers=HEADERS,
        )
    assert response.status_code == 404
    assert response.json()["detail"] == "Well not found"


def test_forecast_date_start_after_date_end():
    response = client.get(
        "/api/v1/forecast",
        params={"id_well": "POZO-001", "date_start": "2024-01-10", "date_end": "2024-01-01"},
        headers=HEADERS,
    )
    assert response.status_code == 422


def test_forecast_horizon_too_long():
    """Un rango que supera MAX_FORECAST_DAYS debe rechazarse con 422 (evita respuestas
    gigantes sobre fechas arbitrariamente lejanas)."""
    response = client.get(
        "/api/v1/forecast",
        params={"id_well": "POZO-001", "date_start": "2024-01-01", "date_end": "2030-01-01"},
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert "máximo" in response.json()["detail"]


def test_forecast_horizon_at_limit_ok():
    """Un rango dentro del límite sigue funcionando (borde: 366 días)."""
    response = client.get(
        "/api/v1/forecast",
        params={"id_well": "POZO-001", "date_start": "2024-01-01", "date_end": "2025-01-01"},
        headers=HEADERS,
    )
    assert response.status_code == 200


def test_forecast_no_api_key():
    response = client.get(
        "/api/v1/forecast",
        params={"id_well": "POZO-001", "date_start": "2024-01-01", "date_end": "2024-01-05"},
    )
    assert response.status_code == 403


def test_forecast_invalid_api_key():
    response = client.get(
        "/api/v1/forecast",
        params={"id_well": "POZO-001", "date_start": "2024-01-01", "date_end": "2024-01-05"},
        headers={"X-API-Key": "wrong"},
    )
    assert response.status_code == 403


def test_forecast_missing_params():
    response = client.get("/api/v1/forecast", headers=HEADERS)
    assert response.status_code == 422


def test_forecast_production_decreases():
    response = client.get(
        "/api/v1/forecast",
        params={"id_well": "POZO-001", "date_start": "2024-01-01", "date_end": "2024-01-03"},
        headers=HEADERS,
    )
    data = response.json()["data"]
    assert data[0]["prod"] > data[1]["prod"] > data[2]["prod"]
