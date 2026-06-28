"""Tests de la materialización del feature store (build_store_features).

La lógica reusa `ml/features` (sklearn) → se saltea si sklearn no está disponible.
Valida estructura y comportamiento clave (columnas, idpozo int, target left-join,
`mes` = mes del target) sobre un panel sintético, sin red ni DB.
"""
import pandas as pd
import pytest

pytest.importorskip("sklearn")  # add_engineered_features usa NearestNeighbors

from data_pipeline.orchestration import feature_store_build as fsb  # noqa: E402
from ml import features as mlf  # noqa: E402
from ml.dataset import BASIC_CATEGORICAL_FEATURES, BASIC_NUMERIC_FEATURES  # noqa: E402


def _panel_crudo(n_pozos=6, meses=5) -> pd.DataFrame:
    """Panel mensual sintético con TODAS las columnas crudas (como vendría de Bronze)."""
    filas = []
    for p in range(n_pozos):
        for m in range(1, meses + 1):
            fila = {
                "idpozo": str(100 + p), "anio": "2020", "mes": str(m),
                "prod_pet": str(10.0 + p + m), "prod_gas": "5", "prod_agua": "2",
                "tef": "30", "profundidad": "2500",
                "coordenadax": str(1.0 + p), "coordenaday": str(2.0 + p),
            }
            for c in BASIC_CATEGORICAL_FEATURES:
                fila[c] = f"{c}_v"
            filas.append(fila)
    return pd.DataFrame(filas)[fsb._RAW_COLS]


def test_columnas_y_grano():
    out = fsb.build_store_features(_panel_crudo())
    esperadas = (
        ["idpozo", "periodo", "periodo_objetivo"]
        + BASIC_NUMERIC_FEATURES + mlf.ENGINEERED_FEATURES
        + BASIC_CATEGORICAL_FEATURES + ["y_next"]
    )
    assert list(out.columns) == esperadas
    assert str(out["idpozo"].dtype).startswith("int")
    assert len(out) == 6 * 5  # left-join: conserva todas las filas


def test_target_left_join_y_mes_es_del_objetivo():
    out = fsb.build_store_features(_panel_crudo()).sort_values(["idpozo", "periodo"])
    un_pozo = out[out.idpozo == 100].reset_index(drop=True)
    # y_next = prod_pet del mes siguiente; la última fila queda NULL (para inferencia)
    assert un_pozo["y_next"].iloc[:-1].notna().all()
    assert pd.isna(un_pozo["y_next"].iloc[-1])
    assert un_pozo["y_next"].iloc[0] == un_pozo["prod_pet"].iloc[1]
    # `mes` = mes del MES OBJETIVO (t+1): para periodo 2020-01 → mes 2
    assert un_pozo["mes"].iloc[0] == 2
