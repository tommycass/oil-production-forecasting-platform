from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.core.rate_limit import limiter

client = TestClient(app)

API_KEY = "test-key"
HEADERS = {"X-API-Key": API_KEY}


def test_rate_limit_returns_429_after_exceeding_limit():
    limiter.reset()
    try:
        with patch("app.core.security.API_KEY", API_KEY):
            for i in range(60):
                response = client.get(
                    "/api/v1/wells",
                    params={"date_query": "2024-01-01"},
                    headers=HEADERS,
                )
                assert response.status_code == 200, (
                    f"request {i + 1}/60 should have passed, got {response.status_code}"
                )

            over_limit = client.get(
                "/api/v1/wells",
                params={"date_query": "2024-01-01"},
                headers=HEADERS,
            )
            assert over_limit.status_code == 429
    finally:
        limiter.reset()
