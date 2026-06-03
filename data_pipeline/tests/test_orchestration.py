"""Tests de la capa de orquestación (Dagster).

Verifican la configuración de los assets sin ejecutarlos (no tocan la red):
que la política de reintentos con backoff exponencial esté aplicada.
"""

import pandas as pd
import pytest
from dagster import Backoff, materialize

from data_pipeline.extraction import extract_produccion as extract_mod
from data_pipeline.orchestration.assets import bronze_pozos, bronze_produccion

# CSV de prueba con BOM: 3 filas en 2 particiones (2020-01 con dos, 2020-02 con una).
_CSV_CON_BOM = (
    "﻿idempresa,anio,mes,idpozo,prod_gas,rectificado,fecha_data\n"
    "YPF,2020,1,100,50.5,f,2020-02-01\n"
    "YPF,2020,1,101,10.0,f,2020-02-01\n"
    "YSUR,2020,2,200,5.5,t,2020-03-01\n"
).encode("utf-8")


class _FakeResponse:
    content = _CSV_CON_BOM

    def raise_for_status(self) -> None:
        pass


@pytest.mark.parametrize("asset_def", [bronze_pozos, bronze_produccion])
def test_asset_tiene_retry_con_backoff_exponencial(asset_def):
    retry_policy = asset_def.op.retry_policy
    assert retry_policy is not None
    assert retry_policy.max_retries == 3
    assert retry_policy.backoff == Backoff.EXPONENTIAL
    assert retry_policy.delay == 5


def test_backfill_rematerializar_es_idempotente(tmp_path, monkeypatch):
    """Re-materializar el asset (reproceso/backfill) no duplica datos."""
    monkeypatch.setattr(extract_mod, "BRONZE_DIR", tmp_path)
    monkeypatch.setattr(extract_mod.requests, "get", lambda *a, **k: _FakeResponse())

    # Materializar dos veces simula reprocesar el mismo período (backfill).
    assert materialize([bronze_produccion]).success
    assert materialize([bronze_produccion]).success

    parquets = list((tmp_path / "produccion").rglob("produccion.parquet"))
    assert len(parquets) == 2  # una partición por mes, sin acumular
    total = sum(len(pd.read_parquet(p)) for p in parquets)
    assert total == 3  # sin filas duplicadas
