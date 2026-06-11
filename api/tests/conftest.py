import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def stub_dw():
    """Ningún test toca el DW real: por defecto `fetch_all` devuelve `[]`.

    Así los tests de auth/rate-limit que pegan a `/wells` reciben 200 sin base.
    Los tests que necesitan datos del DW overridean este stub con su propio patch.
    """
    with patch("app.services.wells.fetch_all", return_value=[]):
        yield