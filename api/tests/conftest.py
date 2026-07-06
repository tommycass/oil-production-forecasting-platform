import sys
from pathlib import Path

# La API importa `ml/` (raíz del repo) en el camino on-the-fly de /forecast
# (`from ml import forecast`). pytest solo agrega `api/` al sys.path (raíz del
# paquete de tests), así que la raíz del repo hay que sumarla a mano — si no,
# `pytest api/tests/` desde la raíz (como corre CI) falla con ModuleNotFoundError.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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