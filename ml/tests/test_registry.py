"""Tests de la decisión de promoción a Production (criterio ADR-039, Rol 3 1.5).

Se testea la **función pura** ``promotion_decision`` en aislamiento (sin MLflow ni
datos): dado el test RMSE del candidato, el de la persistencia y el del Production
actual, decide si promueve. Es la regla de negocio del despliegue automático del
modelo, así que conviene fijarla con tests.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from ml import registry
from ml.registry import promotion_decision


def test_no_promueve_si_no_supera_persistencia():
    """La vara de éxito (ADR-029): sin superar la persistencia, no se promueve —
    aunque no haya Production actual."""
    promover, motivo = promotion_decision(
        nuevo_test_rmse=170.0, persistencia_test_rmse=166.0, prod_test_rmse=None
    )
    assert promover is False
    assert "persistencia" in motivo


def test_promueve_primer_campeon_si_supera_persistencia():
    """Sin Production previo y superando la persistencia: se promueve el primer campeón."""
    promover, motivo = promotion_decision(
        nuevo_test_rmse=150.0, persistencia_test_rmse=166.0, prod_test_rmse=None
    )
    assert promover is True
    assert "Production" in motivo


def test_promueve_si_mejora_al_production_actual():
    """Con Production previo: promueve solo si mejora su test RMSE."""
    promover, _ = promotion_decision(
        nuevo_test_rmse=150.0, persistencia_test_rmse=166.0, prod_test_rmse=154.0
    )
    assert promover is True


def test_no_promueve_si_no_mejora_al_production_actual():
    """Supera la persistencia pero no mejora al Production actual: no se promueve."""
    promover, motivo = promotion_decision(
        nuevo_test_rmse=160.0, persistencia_test_rmse=166.0, prod_test_rmse=154.0
    )
    assert promover is False
    assert "no mejora" in motivo


def test_empate_no_promueve():
    """Empate estricto (no es 'mejor que'): no se promueve (criterio conservador)."""
    promover, _ = promotion_decision(
        nuevo_test_rmse=154.0, persistencia_test_rmse=166.0, prod_test_rmse=154.0
    )
    assert promover is False


# --- RMSE del Production vigente re-evaluado en vivo (ADR-039) --------------------
# De dónde sale `prod_test_rmse`: se carga el modelo vigente y predice sobre la ventana
# del candidato, en vez de leer una métrica vieja guardada como tag.

_X_TEST = pd.DataFrame({"f": [1.0, 2.0, 3.0]})
_Y_TEST = pd.Series([10.0, 20.0, 30.0])


def _client_con_produccion(tags=None):
    """MlflowClient mockeado que reporta un Production vigente con esos tags de versión."""
    client = MagicMock()
    client.get_latest_versions.return_value = [SimpleNamespace(tags=tags or {})]
    return client


def test_incumbent_none_si_no_hay_production():
    """Sin Production vigente: None → el candidato se trata como primer campeón."""
    client = MagicMock()
    client.get_latest_versions.return_value = []
    rmse = registry._incumbent_test_rmse(client, "m", _X_TEST, _Y_TEST)
    assert rmse is None


def test_incumbent_re_evalua_en_vivo_sobre_la_ventana_del_candidato():
    """El RMSE sale de las predicciones del modelo vigente sobre X_test/y_test, no del tag.
    Predicción perfecta → RMSE 0 (ignora el tag 999)."""
    modelo = SimpleNamespace(predict=lambda X: _Y_TEST.to_numpy())  # predice exacto
    with patch.object(registry.mlflow.sklearn, "load_model", return_value=modelo):
        rmse = registry._incumbent_test_rmse(
            _client_con_produccion({"test_rmse": "999.0"}), "m", _X_TEST, _Y_TEST
        )
    assert rmse == 0.0


def test_incumbent_fallback_al_tag_si_falla_la_carga():
    """Si el artefacto vigente no carga/predice, no rompe: cae al test_rmse del tag."""
    with patch.object(registry.mlflow.sklearn, "load_model", side_effect=RuntimeError("boom")):
        rmse = registry._incumbent_test_rmse(
            _client_con_produccion({"test_rmse": "154.0"}), "m", _X_TEST, _Y_TEST
        )
    assert rmse == 154.0
