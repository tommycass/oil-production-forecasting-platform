"""Tests unitarios de la extracción de pozos → Bronze.

No tocan la red: simulan la descarga mockeando `requests.get` con un CSV de
prueba. Verifican el comportamiento que nos importa del crudo en Bronze
(descarte de BOM, datos como texto, ruta versionada por fecha, idempotencia).
"""

from datetime import date

import pandas as pd
import pytest

from data_pipeline.extraction import extract_pozos as mod

# CSV de prueba con BOM (﻿) y el schema COMPLETO de pozos (todas las columnas que
# valida la ingesta, ver config.EXPECTED_COLUMNS["pozos"]).
_CSV_CON_BOM = (
    "﻿idpozo,sigla,formprod,idempresa,idareayacimiento,areayacimiento,cuenca,"
    "provincia,profundidad,coordenadax,coordenaday,clasificacion,tipo_reservorio\n"
    "144081,315,FM1,EMP1,AY1,AREA1,NEUQUINA,NEUQUEN,2500,1.0,2.0,EXPLOTACION,SHALE\n"
    "144082,316,FM1,EMP1,AY1,AREA1,NEUQUINA,NEUQUEN,2600,1.5,2.5,EXPLOTACION,SHALE\n"
).encode("utf-8")


class _FakeResponse:
    """Respuesta HTTP mínima para reemplazar la de requests en el test."""

    content = _CSV_CON_BOM

    def raise_for_status(self) -> None:
        pass


@pytest.fixture
def bronze_tmp(tmp_path, monkeypatch):
    """Redirige la capa Bronze a un directorio temporal y mockea la descarga."""
    monkeypatch.setattr(mod, "BRONZE_DIR", tmp_path)
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _FakeResponse())
    return tmp_path


def test_descarta_bom_de_la_primera_columna(bronze_tmp):
    archivo = mod.extract_pozos(ingesta=date(2026, 6, 1))
    df = pd.read_parquet(archivo)
    # La primera columna debe ser "idpozo", no "﻿idpozo" (BOM descartado).
    assert df.columns[0] == "idpozo"
    assert df["idpozo"].tolist() == ["144081", "144082"]


def test_guarda_todo_como_texto(bronze_tmp):
    archivo = mod.extract_pozos(ingesta=date(2026, 6, 1))
    df = pd.read_parquet(archivo)
    # Bronze es crudo: ningún tipo se infiere, todo queda como texto.
    # is_string_dtype acepta tanto 'object' (pandas clásico) como 'str'/'string[pyarrow]'.
    assert all(pd.api.types.is_string_dtype(dt) for dt in df.dtypes)


def test_ruta_versionada_por_fecha_de_ingesta(bronze_tmp):
    archivo = mod.extract_pozos(ingesta=date(2026, 6, 1))
    assert archivo == bronze_tmp / "pozos" / "ingesta=2026-06-01" / "pozos.parquet"
    assert archivo.exists()


def test_idempotente_misma_fecha(bronze_tmp):
    mod.extract_pozos(ingesta=date(2026, 6, 1))
    archivo = mod.extract_pozos(ingesta=date(2026, 6, 1))  # segunda corrida
    # Sigue habiendo un solo parquet para esa ingesta, sin filas duplicadas.
    parquets = list((bronze_tmp / "pozos" / "ingesta=2026-06-01").glob("*.parquet"))
    assert len(parquets) == 1
    assert len(pd.read_parquet(archivo)) == 2
