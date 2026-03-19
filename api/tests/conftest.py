import os

# Debe setearse ANTES de que la app importe security.py
os.environ["API_KEY"] = "abcdef12345"

import pytest
from fastapi.testclient import TestClient
from app.main import app

VALID_HEADERS = {"X-API-Key": "abcdef12345"}
INVALID_HEADERS = {"X-API-Key": "wrong-key"}


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return VALID_HEADERS


@pytest.fixture
def bad_headers():
    return INVALID_HEADERS