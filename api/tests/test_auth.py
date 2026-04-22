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


def test_api_key_header_name_is_case_insensitive():
    with patch("app.core.security.API_KEY", API_KEY):
        lowercase = client.get(
            "/api/v1/wells",
            params={"date_query": "2024-01-01"},
            headers={"x-api-key": API_KEY},
        )
        mixed_case = client.get(
            "/api/v1/wells",
            params={"date_query": "2024-01-01"},
            headers={"X-Api-Key": API_KEY},
        )
    assert lowercase.status_code != 403
    assert mixed_case.status_code != 403


def test_forbidden_response_body_is_well_formed():
    with patch("app.core.security.API_KEY", API_KEY):
        missing = client.get(
            "/api/v1/wells",
            params={"date_query": "2024-01-01"},
        )
        invalid = client.get(
            "/api/v1/wells",
            params={"date_query": "2024-01-01"},
            headers={"X-API-Key": "wrong-key"},
        )
    for response in (missing, invalid):
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {"detail": "Forbidden"}