"""Tests de la validación de contrato de schema en la ingesta (ADR-022)."""

import pandas as pd
import pytest

from data_pipeline.config import EXPECTED_COLUMNS
from data_pipeline.extraction.validation import SchemaContractError, validar_columnas


def _df_con(columnas) -> pd.DataFrame:
    """DataFrame de una fila con exactamente esas columnas."""
    return pd.DataFrame({c: ["x"] for c in columnas})


def test_schema_completo_pasa():
    # Con todas las columnas esperadas, no levanta nada.
    validar_columnas(_df_con(EXPECTED_COLUMNS["produccion"]), "produccion")
    validar_columnas(_df_con(EXPECTED_COLUMNS["pozos"]), "pozos")


def test_columna_requerida_faltante_corta():
    cols = EXPECTED_COLUMNS["produccion"] - {"prod_gas"}
    with pytest.raises(SchemaContractError, match="prod_gas"):
        validar_columnas(_df_con(cols), "produccion")


def test_columna_de_mas_solo_avisa(capsys):
    cols = set(EXPECTED_COLUMNS["pozos"]) | {"columna_nueva"}
    validar_columnas(_df_con(cols), "pozos")  # no levanta
    salida = capsys.readouterr().out
    assert "columna_nueva" in salida and "no esperadas" in salida
