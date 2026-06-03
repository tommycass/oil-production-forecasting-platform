"""Tests de la capa de orquestación (Dagster).

Verifican la configuración de los assets sin ejecutarlos (no tocan la red):
que la política de reintentos con backoff exponencial esté aplicada.
"""

import pytest
from dagster import Backoff

from data_pipeline.orchestration.assets import bronze_pozos, bronze_produccion


@pytest.mark.parametrize("asset_def", [bronze_pozos, bronze_produccion])
def test_asset_tiene_retry_con_backoff_exponencial(asset_def):
    retry_policy = asset_def.op.retry_policy
    assert retry_policy is not None
    assert retry_policy.max_retries == 3
    assert retry_policy.backoff == Backoff.EXPONENTIAL
    assert retry_policy.delay == 5
