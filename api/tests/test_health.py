from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_returns_200():
    response = client.get("/health")
    assert response.status_code == 200


def test_health_does_not_require_api_key():
    with patch("app.core.security.API_KEY", "some-configured-key"):
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}