import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app

client = TestClient(app)

API_KEY = "test-key"
HEADERS = {"X-API-Key": API_KEY}

# Muestra representativa de las features que devuelve el store (el modelo va mockeado,
# así que los valores no importan; sí que el reader devuelva un dict de features).
FAKE_FEATURES = {
    "prod_pet": 500.0, "prod_gas": 1200.0, "prod_agua": 30.0, "tef": 0.9,
    "profundidad": 2500.0, "coordenadax": 2.5e6, "coordenaday": 5.7e6, "mes": 6,
    "prod_pet_roll3": 480.0, "prod_pet_delta1": 5.0, "prod_pet_lag12": 510.0,
    "prod_pet_acum6": 2900.0, "water_cut": 0.06, "produjo_mes_pasado": 1,
    "prod_vecinos_mean": 460.0, "cuenca": "Neuquina", "provincia": "Neuquén",
}


@pytest.fixture(autouse=True)
def mock_api_key():
    with patch("app.core.security.API_KEY", API_KEY):
        yield


def _mock_loader(version="3", model_name="produccion-forecast", prediction=423.7):
    loader = MagicMock()
    loader.predict.return_value = prediction
    loader.version = version
    loader.model_name = model_name
    return loader


def test_predict_success_petroleo():
    """Target por defecto (petróleo): 200 con predicción, target y modelo servido."""
    with patch("app.routes.predict.get_features_for_inference", return_value=FAKE_FEATURES), \
         patch("app.routes.predict.get_loader", return_value=_mock_loader()):
        response = client.post(
            "/api/v1/predict",
            json={"idpozo": 507, "anio": 2026, "mes": 6},
            headers=HEADERS,
        )
    assert response.status_code == 200
    data = response.json()
    assert data["prediccion"] == 423.7
    assert data["target"] == "prod_pet"
    assert data["model_name"] == "produccion-forecast"
    assert data["model_version"] == "3"
    assert data["model_stage"] == "Production"
    assert data["idpozo"] == 507


def test_predict_success_gas():
    """Target gas: enruta al modelo de gas y devuelve target=prod_gas."""
    gas_loader = _mock_loader(version="2", model_name="produccion-forecast-gas", prediction=1500.5)
    with patch("app.routes.predict.get_features_for_inference", return_value=FAKE_FEATURES) as reader, \
         patch("app.routes.predict.get_loader", return_value=gas_loader) as loader_getter:
        response = client.post(
            "/api/v1/predict",
            json={"idpozo": 507, "anio": 2026, "mes": 6, "target": "prod_gas"},
            headers=HEADERS,
        )
    assert response.status_code == 200
    data = response.json()
    assert data["target"] == "prod_gas"
    assert data["prediccion"] == 1500.5
    assert data["model_name"] == "produccion-forecast-gas"
    # el target se propaga al reader y al selector de modelo
    reader.assert_called_once_with(507, 2026, 6, "prod_gas")
    loader_getter.assert_called_once_with("prod_gas")


def test_predict_well_not_found():
    with patch("app.routes.predict.get_features_for_inference", side_effect=ValueError("sin datos")):
        response = client.post(
            "/api/v1/predict",
            json={"idpozo": 9999, "anio": 2026, "mes": 6},
            headers=HEADERS,
        )
    assert response.status_code == 404


def test_predict_model_unavailable():
    unavailable = MagicMock()
    unavailable.predict.side_effect = RuntimeError("Modelo no disponible")
    unavailable.version = None
    unavailable.model_name = "produccion-forecast"
    with patch("app.routes.predict.get_features_for_inference", return_value=FAKE_FEATURES), \
         patch("app.routes.predict.get_loader", return_value=unavailable):
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


def test_predict_invalid_target():
    """Un target fuera de {prod_pet, prod_gas} es rechazado por validación (422)."""
    response = client.post(
        "/api/v1/predict",
        json={"idpozo": 507, "anio": 2026, "mes": 6, "target": "prod_oro"},
        headers=HEADERS,
    )
    assert response.status_code == 422
