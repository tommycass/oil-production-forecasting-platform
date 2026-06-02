"""Tests unitarios de la extracción de producción → Bronze.

No tocan la red: simulan la descarga mockeando `requests.get` con un CSV de
prueba. Verifican lo propio de producción: particionado por anio/mes, descarte
de BOM, conservación del crudo e idempotencia (full refresh).
"""

import pandas as pd
import pytest

from data_pipeline.extraction import extract_produccion as mod

# CSV de prueba con BOM (﻿). Tres filas en dos particiones: (2020,1) con dos
# filas y (2020,2) con una. Incluye rectificado y fecha_data (contrato con Silver).
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
def bronze_tmp(tmp_path, monkeypatch):
    """Redirige Bronze a un directorio temporal y mockea la descarga."""
    monkeypatch.setattr(mod, "BRONZE_DIR", tmp_path)
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _FakeResponse())
    return tmp_path


def test_particiona_por_anio_mes(bronze_tmp):
    mod.extract_produccion()
    p1 = bronze_tmp / "produccion" / "anio=2020" / "mes=1" / "produccion.parquet"
    p2 = bronze_tmp / "produccion" / "anio=2020" / "mes=2" / "produccion.parquet"
    assert p1.exists() and p2.exists()
    # Cada partición tiene exactamente las filas de su mes.
    assert len(pd.read_parquet(p1)) == 2
    assert len(pd.read_parquet(p2)) == 1


def test_descarta_bom_y_conserva_crudo(bronze_tmp):
    mod.extract_produccion()
    df = pd.read_parquet(bronze_tmp / "produccion" / "anio=2020" / "mes=1" / "produccion.parquet")
    # BOM descartado: la primera columna es "idempresa", no "﻿idempresa".
    assert df.columns[0] == "idempresa"
    # Crudo fiel: todo texto y se conservan las columnas que usa Silver.
    assert set(df.dtypes.astype(str)) == {"object"}
    assert {"rectificado", "fecha_data"}.issubset(df.columns)


def test_idempotente_full_refresh(bronze_tmp):
    mod.extract_produccion()
    mod.extract_produccion()  # segunda corrida
    parquets = list((bronze_tmp / "produccion").rglob("produccion.parquet"))
    # Sigue habiendo una sola partición por mes (2) y sin filas duplicadas.
    assert len(parquets) == 2
    total = sum(len(pd.read_parquet(p)) for p in parquets)
    assert total == 3
