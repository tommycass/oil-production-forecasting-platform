"""Tests de ``/forecast`` — pronóstico recursivo mensual (ADR-044).

El endpoint mantiene el **contrato de Fase 1** (``id_well``, ``date_start``, ``date_end``
→ ``{id_well, data:[{date, prod}]}``) y agrega el parámetro **opcional** ``target``
(default ``prod_pet``). La salida es **mensual** (un punto por mes futuro).

Se mockean las dos dependencias externas: la **lectura del feature store**
(``get_history_for_forecast``) y el **modelo** (``get_loader``). El motor recursivo
(``ml.forecast``) corre de verdad sobre la historia mockeada.
"""
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from app.main import app

client = TestClient(app)

API_KEY = "test-key"
HEADERS = {"X-API-Key": API_KEY}


@pytest.fixture(autouse=True)
def mock_api_key():
    with patch("app.core.security.API_KEY", API_KEY):
        yield


@pytest.fixture(autouse=True)
def mock_well_exists():
    """Evita queries al DW: well_exists_in_dw retorna True por defecto."""
    with patch("app.routes.forecast.well_exists_in_dw", return_value=True):
        yield


def _history(last="2025-06-01", n=12, target="prod_pet"):
    """Serie mensual observada sintética, terminando en ``last`` (L = último mes)."""
    fin = pd.Timestamp(last)
    periodos = pd.date_range(end=fin, periods=n, freq="MS")
    return pd.DataFrame({"periodo": periodos, target: [1000 - 20 * i for i in range(n)]})


_STATIC = {
    "idpozo": 42, "profundidad": 2000.0, "coordenadax": 1.0, "coordenaday": 2.0,
    "empresa": "ACME", "tipopozo": "Pet", "areayacimiento": "YAC",
}


def _loader(target="prod_pet"):
    """Loader stub: su modelo declina 2% el último valor del target (usa la realimentación)."""
    loader = MagicMock()
    loader.model_name = "produccion-forecast"
    loader.version = "3"
    model = MagicMock()
    model.feature_names_in_ = None
    model.predict = lambda X: [float(X[target].iloc[-1]) * 0.98]
    loader.snapshot_model.return_value = model
    return loader


def _base_features(series, target):
    """Fila del mes base como la daría el store: último valor del target + estáticos."""
    return {target: float(series[target].iloc[-1]), **_STATIC}


def _patch(history=None, loader=None, target="prod_pet"):
    """Mockea el seam del feature store (base_features, series, static) y el modelo."""
    series = _history(target=target) if history is None else history
    loader = _loader(target) if loader is None else loader
    return (
        patch(
            "app.services.forecast.get_history_for_forecast",
            return_value=(_base_features(series, target), series, _STATIC),
        ),
        patch("app.services.forecast.get_loader", return_value=loader),
    )


def _get(params):
    return client.get("/api/v1/forecast", params=params, headers=HEADERS)


# --- camino feliz -----------------------------------------------------------

def test_forecast_success_monthly():
    """Rango futuro normal: devuelve un punto por mes, con fecha = 1° de cada mes."""
    ph, pl = _patch()
    with ph, pl:
        r = _get({"id_well": "42", "date_start": "2025-07-01", "date_end": "2025-09-15"})
    assert r.status_code == 200
    body = r.json()
    assert body["id_well"] == "42"
    fechas = [p["date"] for p in body["data"]]
    assert fechas == ["2025-07-01", "2025-08-01", "2025-09-01"]  # mensual, futuro
    assert all(isinstance(p["prod"], (int, float)) for p in body["data"])


def test_forecast_future_only():
    """date_start en el pasado: el pronóstico arranca en último_observado + 1, no antes."""
    ph, pl = _patch()
    with ph, pl:
        r = _get({"id_well": "42", "date_start": "2020-01-01", "date_end": "2025-08-01"})
    assert r.status_code == 200
    assert [p["date"] for p in r.json()["data"]] == ["2025-07-01", "2025-08-01"]


def test_forecast_truncated_to_horizon():
    """Rango que supera el horizonte máximo: se recorta a 12 meses desde el último dato."""
    ph, pl = _patch()
    with ph, pl:
        r = _get({"id_well": "42", "date_start": "2025-07-01", "date_end": "2030-01-01"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 12  # L=2025-06 -> hasta 2026-06
    assert data[-1]["date"] == "2026-06-01"


def test_forecast_declines_recursively():
    """El modelo stub declina; la serie pronosticada debe ser monótona decreciente."""
    ph, pl = _patch()
    with ph, pl:
        r = _get({"id_well": "42", "date_start": "2025-07-01", "date_end": "2025-10-01"})
    prods = [p["prod"] for p in r.json()["data"]]
    assert prods == sorted(prods, reverse=True)


def test_forecast_gas_target():
    """target=prod_gas usa el modelo/serie de gas (parámetro opcional)."""
    hist = _history(target="prod_gas")
    ph, pl = _patch(history=hist, loader=_loader("prod_gas"), target="prod_gas")
    with ph, pl:
        r = _get({
            "id_well": "42", "date_start": "2025-07-01", "date_end": "2025-08-01",
            "target": "prod_gas",
        })
    assert r.status_code == 200
    assert len(r.json()["data"]) == 2


def test_forecast_default_target_is_pet():
    """Sin `target` el default es petróleo: mismo resultado que pasándolo explícito."""
    ph, pl = _patch()
    with ph, pl:
        r = _get({"id_well": "42", "date_start": "2025-07-01", "date_end": "2025-08-01"})
    assert r.status_code == 200


# --- casos borde ------------------------------------------------------------

def test_forecast_all_past_is_422():
    """Rango enteramente en el pasado (sin meses futuros): 422."""
    ph, pl = _patch()
    with ph, pl:
        r = _get({"id_well": "42", "date_start": "2024-01-01", "date_end": "2025-06-01"})
    assert r.status_code == 422


def test_forecast_start_beyond_horizon_is_422():
    """El rango empieza más allá del horizonte máximo: 422 (no hay nada que devolver)."""
    ph, pl = _patch()
    with ph, pl:
        r = _get({"id_well": "42", "date_start": "2027-01-01", "date_end": "2027-06-01"})
    assert r.status_code == 422


def test_forecast_date_start_after_date_end():
    r = _get({"id_well": "42", "date_start": "2025-10-01", "date_end": "2025-07-01"})
    assert r.status_code == 422


def test_forecast_well_not_found():
    """Pozo inexistente en el DW: 404 (no llega a tocar el feature store)."""
    with patch("app.routes.forecast.well_exists_in_dw", return_value=False):
        r = _get({"id_well": "999", "date_start": "2025-07-01", "date_end": "2025-08-01"})
    assert r.status_code == 404


def test_forecast_well_no_history_is_404():
    """Pozo en el DW pero sin serie en el feature store (fuera del universo): 404."""
    with patch(
        "app.services.forecast.get_history_for_forecast",
        side_effect=ValueError("sin historia"),
    ):
        r = _get({"id_well": "42", "date_start": "2025-07-01", "date_end": "2025-08-01"})
    assert r.status_code == 404


def test_forecast_model_not_loaded_is_503():
    """Modelo no disponible en MLflow: 503."""
    loader = MagicMock()
    loader.snapshot_model.side_effect = RuntimeError("modelo no disponible")
    ph, _ = _patch()
    with ph, patch("app.services.forecast.get_loader", return_value=loader):
        r = _get({"id_well": "42", "date_start": "2025-07-01", "date_end": "2025-08-01"})
    assert r.status_code == 503


def test_forecast_invalid_target_is_422():
    """Un target fuera de {prod_pet, prod_gas} lo rechaza la validación de FastAPI (422)."""
    r = _get({
        "id_well": "42", "date_start": "2025-07-01", "date_end": "2025-08-01",
        "target": "prod_agua",
    })
    assert r.status_code == 422


# --- auth / params ----------------------------------------------------------

def test_forecast_no_api_key():
    r = client.get(
        "/api/v1/forecast",
        params={"id_well": "42", "date_start": "2025-07-01", "date_end": "2025-08-01"},
    )
    assert r.status_code == 403


def test_forecast_invalid_api_key():
    r = client.get(
        "/api/v1/forecast",
        params={"id_well": "42", "date_start": "2025-07-01", "date_end": "2025-08-01"},
        headers={"X-API-Key": "wrong"},
    )
    assert r.status_code == 403


def test_forecast_missing_params():
    r = client.get("/api/v1/forecast", headers=HEADERS)
    assert r.status_code == 422
