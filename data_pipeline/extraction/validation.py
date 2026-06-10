"""Validación del contrato de schema en la ingesta (fail-fast).

Chequea que el CSV descargado de datos.gob.ar traiga las columnas esperadas ANTES de
persistir en Bronze. Si la fuente cambió su estructura (faltan columnas que Silver
consume), se aborta la extracción —y con ella el DAG— en vez de propagar una estructura
rota aguas abajo y enterarse recién en dbt. Es el chequeo de schema en el límite de
confianza con la fuente externa; complementa el check de schema de Silver (ADR-016).
Ver ADR-022.
"""

from __future__ import annotations

import pandas as pd

from data_pipeline.config import EXPECTED_COLUMNS


class SchemaContractError(ValueError):
    """La fuente no cumple el contrato de columnas esperado (falta alguna)."""


def validar_columnas(df: pd.DataFrame, fuente: str) -> None:
    """Valida que `df` traiga las columnas esperadas para `fuente`.

    - Falta alguna columna esperada → lanza `SchemaContractError` (corta el DAG).
    - Aparecen columnas de más → solo avisa por log (cambio aditivo, no corta).

    Args:
        df: DataFrame recién leído del CSV de la fuente.
        fuente: clave de la fuente en `EXPECTED_COLUMNS` ("produccion" | "pozos").
    """
    esperadas = EXPECTED_COLUMNS[fuente]
    presentes = set(df.columns)

    faltantes = esperadas - presentes
    if faltantes:
        raise SchemaContractError(
            f"[{fuente}] la fuente cambió su schema: faltan columnas esperadas "
            f"{sorted(faltantes)}. Se aborta la ingesta para no romper Bronze/Silver "
            f"(ver ADR-022)."
        )

    extra = presentes - esperadas
    if extra:
        print(f"[{fuente}] aviso: columnas nuevas no esperadas (no corta): {sorted(extra)}")
