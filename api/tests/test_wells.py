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


def test_wells_success():
    # El DW se mockea: el test verifica el contrato del endpoint, no la base real.
    fake_rows = [{"id_well": "4815"}, {"id_well": "4816"}]
    with patch("app.services.wells.fetch_all", return_value=fake_rows):
        response = client.get(
            "/api/v1/wells",
            params={"date_query": "2024-01-01"},
            headers=HEADERS,
        )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["id_well"] == "4815"


def test_wells_future_date():
    response = client.get(
        "/api/v1/wells",
        params={"date_query": "2099-01-01"},
        headers=HEADERS,
    )
    assert response.status_code == 422


def test_wells_no_api_key():
    response = client.get(
        "/api/v1/wells",
        params={"date_query": "2024-01-01"},
    )
    assert response.status_code == 403


def test_wells_invalid_api_key():
    response = client.get(
        "/api/v1/wells",
        params={"date_query": "2024-01-01"},
        headers={"X-API-Key": "wrong"},
    )
    assert response.status_code == 403


def test_wells_missing_date_query():
    response = client.get("/api/v1/wells", headers=HEADERS)
    assert response.status_code == 422
