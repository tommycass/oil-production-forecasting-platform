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


def test_rate_limit_response_body_matches_custom_handler():
    limiter.reset()
    try:
        with patch("app.core.security.API_KEY", API_KEY):
            for _ in range(60):
                client.get(
                    "/api/v1/wells",
                    params={"date_query": "2024-01-01"},
                    headers=HEADERS,
                )
            over_limit = client.get(
                "/api/v1/wells",
                params={"date_query": "2024-01-01"},
                headers=HEADERS,
            )
            assert over_limit.status_code == 429
            assert over_limit.headers["content-type"].startswith("application/json")
            assert over_limit.json() == {"detail": "Rate limit exceeded. Try again later."}
    finally:
        limiter.reset()


def test_metrics_endpoint_is_not_rate_limited():
    limiter.reset()
    try:
        for i in range(120):
            response = client.get("/metrics")
            assert response.status_code == 200, (
                f"request {i + 1}/120 to /metrics should not be rate limited, "
                f"got {response.status_code}"
            )
    finally:
        limiter.reset()
