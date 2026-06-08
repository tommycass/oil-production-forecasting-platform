"""Tests de la capa de orquestación (Dagster).

Verifican la configuración de los assets (retries) y el comportamiento del
backfill por partición materializando assets sin tocar la red.
"""

import pandas as pd
import pytest
from dagster import AssetKey, Backoff, materialize

from data_pipeline.extraction import extract_produccion as extract_mod
from data_pipeline.orchestration.assets import (
    _DwDbtTranslator,
    bronze_pozos,
    bronze_pozos_db,
    bronze_produccion,
    bronze_produccion_db,
    produccion_raw,
)

# CSV de prueba con BOM: 3 filas en 2 meses (2020-01 con dos, 2020-02 con una).
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


@pytest.fixture
def entorno_tmp(tmp_path, monkeypatch):
    """Redirige landing y Bronze a temporales y mockea la descarga."""
    monkeypatch.setattr(extract_mod, "_LANDING_FILE", tmp_path / "landing" / "produccion.parquet")
    monkeypatch.setattr(extract_mod, "BRONZE_DIR", tmp_path / "bronze")
    monkeypatch.setattr(extract_mod.requests, "get", lambda *a, **k: _FakeResponse())
    return tmp_path


@pytest.mark.parametrize("asset_def", [bronze_pozos, produccion_raw, bronze_produccion])
def test_asset_tiene_retry_con_backoff_exponencial(asset_def):
    retry_policy = asset_def.op.retry_policy
    assert retry_policy is not None
    assert retry_policy.max_retries == 3
    assert retry_policy.backoff == Backoff.EXPONENTIAL
    assert retry_policy.delay == 5


def test_bronze_produccion_es_particionado_por_mes():
    assert bronze_produccion.partitions_def is not None


def test_backfill_de_una_particion_no_toca_las_otras(entorno_tmp):
    # Descargar el landing (raw) y materializar dos meses por separado.
    assert materialize([produccion_raw]).success
    assert materialize([bronze_produccion], partition_key="2020-01-01").success
    assert materialize([bronze_produccion], partition_key="2020-02-01").success

    bronze = entorno_tmp / "bronze" / "produccion"
    p_febrero = bronze / "anio=2020" / "mes=2" / "produccion.parquet"
    mtime_febrero = p_febrero.stat().st_mtime

    # Reprocesar solo enero (backfill de una partición) no toca febrero.
    assert materialize([bronze_produccion], partition_key="2020-01-01").success
    assert p_febrero.stat().st_mtime == mtime_febrero

    # Enero quedó correcto y sin duplicar.
    df_enero = pd.read_parquet(bronze / "anio=2020" / "mes=1" / "produccion.parquet")
    assert len(df_enero) == 2


def test_assets_de_carga_usan_la_key_de_las_dbt_sources():
    # La key de los assets de carga = la dbt source, para que el grafo se conecte.
    assert AssetKey(["bronze", "produccion"]) in bronze_produccion_db.keys
    assert AssetKey(["bronze", "pozos"]) in bronze_pozos_db.keys


def test_carga_depende_del_bronze_parquet():
    deps = bronze_produccion_db.asset_deps[AssetKey(["bronze", "produccion"])]
    assert AssetKey("bronze_produccion") in deps


def test_translator_liga_source_bronze_al_asset_de_carga():
    key = _DwDbtTranslator().get_asset_key(
        {"resource_type": "source", "source_name": "bronze", "name": "produccion"}
    )
    assert key == AssetKey(["bronze", "produccion"])
