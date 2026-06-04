"""Tests unitarios de la extracción de producción → landing + Bronze.

No tocan la red: simulan la descarga mockeando `requests.get` con un CSV de
prueba. Cubren las dos funciones del flujo: descarga del landing y escritura de
una partición (backfill selectivo por mes), además de la corrida completa.
"""

import pandas as pd
import pytest

from data_pipeline.extraction import extract_produccion as mod

# CSV de prueba con BOM (﻿): 3 filas en dos meses (2020-01 con dos, 2020-02 con una).
# Incluye rectificado y fecha_data (contrato con Silver).
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
    monkeypatch.setattr(mod, "_LANDING_FILE", tmp_path / "landing" / "produccion.parquet")
    monkeypatch.setattr(mod, "BRONZE_DIR", tmp_path / "bronze")
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _FakeResponse())
    return tmp_path


def test_descargar_landing_descarta_bom_y_conserva_crudo(entorno_tmp):
    archivo = mod.descargar_landing()
    df = pd.read_parquet(archivo)
    # BOM descartado: primera columna "idempresa", no "﻿idempresa".
    assert df.columns[0] == "idempresa"
    # Crudo fiel: todo texto y se conservan las columnas que usa Silver.
    assert set(df.dtypes.astype(str)) == {"object"}
    assert {"rectificado", "fecha_data"}.issubset(df.columns)
    assert len(df) == 3


def test_escribir_particion_escribe_solo_su_mes(entorno_tmp):
    mod.descargar_landing()
    archivo = mod.escribir_particion("2020", "1")
    df = pd.read_parquet(archivo)
    assert len(df) == 2
    assert set(df["anio"]) == {"2020"} and set(df["mes"]) == {"1"}


def test_backfill_de_un_mes_no_toca_otras_particiones(entorno_tmp):
    mod.extract_produccion_full()
    bronze = entorno_tmp / "bronze" / "produccion"
    p_febrero = bronze / "anio=2020" / "mes=2" / "produccion.parquet"
    mtime_febrero = p_febrero.stat().st_mtime

    # Reprocesar solo enero no debe reescribir la partición de febrero.
    mod.escribir_particion("2020", "1")
    assert p_febrero.stat().st_mtime == mtime_febrero


def test_extract_produccion_full_idempotente(entorno_tmp):
    mod.extract_produccion_full()
    mod.extract_produccion_full()  # segunda corrida
    parquets = list((entorno_tmp / "bronze" / "produccion").rglob("produccion.parquet"))
    assert len(parquets) == 2
    assert sum(len(pd.read_parquet(p)) for p in parquets) == 3
