from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
API_KEY = "test-key"
HEADERS = {"X-API-Key": API_KEY}


def test_missing_api_key_returns_403():
    with patch("app.core.security.API_KEY", API_KEY):
        response = client.get(
            "/api/v1/wells",
            params={"date_query": "2024-01-01"},
        )
    assert response.status_code == 403


def test_invalid_api_key_returns_403():
    with patch("app.core.security.API_KEY", API_KEY):
        response = client.get(
            "/api/v1/wells",
            params={"date_query": "2024-01-01"},
            headers={"X-API-Key": "wrong-key"},
        )
    assert response.status_code == 403


def test_valid_api_key_is_accepted():
    with patch("app.core.security.API_KEY", API_KEY):
        response = client.get(
            "/api/v1/wells",
            params={"date_query": "2024-01-01"},
            headers=HEADERS,
        )
    assert response.status_code != 403