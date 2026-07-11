"""Tests de la orquestación del reentrenamiento (Dagster, ADR-040).

Cubren la estructura del job (partición por día, retries), el comando de
entrenamiento configurable y la lógica de disparo del Schedule y el Sensor. No
tocan red ni DB: el acceso al feature store se mockea.
"""

import pytest
from dagster import (
    Backoff,
    DailyPartitionsDefinition,
    RunRequest,
    SkipReason,
    build_sensor_context,
)

from data_pipeline.orchestration import retrain
from data_pipeline.orchestration.retrain import (
    features_refrescadas,
    forecast_precomputado,
    modelo_reentrenado,
    retrain_mensual,
    retrain_por_features_nuevas,
)


def test_train_cmd_default_es_baseline():
    # Sin RETRAIN_CMD, el paso de entrenamiento orquesta el baseline (ya loguea a MLflow).
    assert retrain._train_cmd()[-2:] == ["-m", "ml.baseline"]


def test_train_cmd_configurable_por_env(monkeypatch):
    monkeypatch.setenv("RETRAIN_CMD", "python -m ml.train --mlflow")
    assert retrain._train_cmd() == ["python", "-m", "ml.train", "--mlflow"]


_ASSETS = [features_refrescadas, modelo_reentrenado, forecast_precomputado]


@pytest.mark.parametrize("asset_def", _ASSETS)
def test_assets_particionados_por_dia(asset_def):
    assert isinstance(asset_def.partitions_def, DailyPartitionsDefinition)


@pytest.mark.parametrize("asset_def", _ASSETS)
def test_assets_tienen_retry(asset_def):
    rp = asset_def.op.retry_policy
    assert rp is not None and rp.backoff == Backoff.EXPONENTIAL


def test_precomputo_es_el_ultimo_paso_del_retrain():
    # El precómputo (ADR-043) depende del entrenamiento: corre con el Production
    # recién promovido, después de refrescar features y reentrenar.
    deps = {k.to_user_string() for k in forecast_precomputado.asset_deps[forecast_precomputado.key]}
    assert "modelo_reentrenado" in deps


def test_schedule_mensual_dia_6():
    assert retrain_mensual.cron_schedule == "0 6 6 * *"


def test_sensor_skip_si_feature_store_inaccesible(monkeypatch):
    monkeypatch.setattr(retrain, "_ultimo_periodo_features", lambda: None)
    assert isinstance(retrain_por_features_nuevas(build_sensor_context(cursor=None)), SkipReason)


def test_sensor_skip_si_no_hay_periodo_nuevo(monkeypatch):
    monkeypatch.setattr(retrain, "_ultimo_periodo_features", lambda: "2026-04-01")
    assert isinstance(
        retrain_por_features_nuevas(build_sensor_context(cursor="2026-04-01")), SkipReason
    )


def test_sensor_dispara_con_periodo_nuevo(monkeypatch):
    monkeypatch.setattr(retrain, "_ultimo_periodo_features", lambda: "2026-04-01")
    assert isinstance(
        retrain_por_features_nuevas(build_sensor_context(cursor="2026-03-01")), RunRequest
    )
