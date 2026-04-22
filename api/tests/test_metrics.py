from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_metrics_endpoint():
    response = client.get("/metrics")
    assert response.status_code == 200
    # Verifica que el cuerpo de respuesta contenga métricas comunes de Prometheus
    assert "http_requests_total" in response.text
    assert "http_request_duration_seconds" in response.text


def test_metrics_does_not_require_api_key():
    with patch("app.core.security.API_KEY", "some-configured-key"):
        response = client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text
