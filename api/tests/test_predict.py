import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app

client = TestClient(app)

API_KEY = "test-key"
HEADERS = {"X-API-Key": API_KEY}
FAKE_FEATURES = {
    "lag1": 500.0, "lag2": 480.0, "lag3": 460.0,
    "roll3": 480.0, "antiguedad": 24, "tef_lag1": 0.9,
}


@pytest.fixture(autouse=True)
def mock_api_key():
    with patch("app.core.security.API_KEY", API_KEY):
        yield


def test_predict_success():
    with patch("app.routes.predict.get_features_for_inference", return_value=FAKE_FEATURES), \
         patch("app.routes.predict.MODEL_LOADER") as mock_loader:
        mock_loader.predict.return_value = 423.7
        mock_loader.version = "3"
        response = client.post(
            "/api/v1/predict",
            json={"idpozo": 507, "anio": 2026, "mes": 6},
            headers=HEADERS,
        )
    assert response.status_code == 200
    data = response.json()
    assert data["prod_pet_predicha"] == 423.7
    assert data["model_version"] == "3"
    assert data["model_stage"] == "Production"
    assert data["idpozo"] == 507


def test_predict_well_not_found():
    with patch("app.routes.predict.get_features_for_inference", side_effect=ValueError("sin datos")):
        response = client.post(
            "/api/v1/predict",
            json={"idpozo": 9999, "anio": 2026, "mes": 6},
            headers=HEADERS,
        )
    assert response.status_code == 404


def test_predict_model_unavailable():
    with patch("app.routes.predict.get_features_for_inference", return_value=FAKE_FEATURES), \
         patch("app.routes.predict.MODEL_LOADER") as mock_loader:
        mock_loader.predict.side_effect = RuntimeError("Modelo no disponible")
        mock_loader.version = None
        response = client.post(
            "/api/v1/predict",
            json={"idpozo": 507, "anio": 2026, "mes": 6},
            headers=HEADERS,
        )
    assert response.status_code == 503


def test_predict_no_api_key():
    response = client.post("/api/v1/predict", json={"idpozo": 507, "anio": 2026, "mes": 6})
    assert response.status_code == 403


def test_predict_missing_field():
    response = client.post(
        "/api/v1/predict",
        json={"idpozo": 507, "anio": 2026},
        headers=HEADERS,
    )
    assert response.status_code == 422


def test_predict_invalid_mes():
    response = client.post(
        "/api/v1/predict",
        json={"idpozo": 507, "anio": 2026, "mes": 13},
        headers=HEADERS,
    )
    assert response.status_code == 422
