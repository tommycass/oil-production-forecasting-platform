"""Tests de la materialización del feature store (build_store_features).

La lógica reusa `ml/features` (sklearn) → se saltea si sklearn no está disponible.
Valida estructura y comportamiento clave (columnas = set FINAL de la selección
ADR-043, idpozo int, target left-join) sobre un panel sintético, sin red ni DB.
Cubre los **dos targets** (petróleo y gas, ADR-042) y el mapeo de tablas.
"""
import pandas as pd
import pytest

pytest.importorskip("sklearn")  # add_engineered_features usa NearestNeighbors

from data_pipeline.orchestration import feature_store_build as fsb  # noqa: E402
from ml import features as mlf  # noqa: E402
from ml.dataset import BASIC_CATEGORICAL_FEATURES  # noqa: E402


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
    # El store materializa EXACTAMENTE el set final de la selección (ADR-043): la
    # misma lista que entrena ml/train.py (`selected_features`) + claves + y_next.
    out = fsb.build_store_features(_panel_crudo())
    esperadas = (
        ["idpozo", "periodo", "periodo_objetivo"]
        + mlf.selected_features("prod_pet") + ["y_next"]
    )
    assert list(out.columns) == esperadas
    assert str(out["idpozo"].dtype).startswith("int")
    assert len(out) == 6 * 5  # left-join: conserva todas las filas


def test_solo_features_seleccionadas():
    # Las descartadas/borderline del ranking (ADR-043) NO se persisten: ni las
    # no-recursion-safe, ni delta3 (ruido), ni las categóricas de imp. ≈ 0, ni `mes`.
    out = fsb.build_store_features(_panel_crudo())
    fuera = {"prod_vecinos_mean", "water_cut", "prod_agua", "tef", "prod_gas",
             "prod_pet_delta3", "prod_pet_lag12", "mes", "empresa", "cuenca",
             "provincia", "formacion", "tipopozo", "produjo_mes_pasado"}
    assert not fuera & set(out.columns)
    # las anclas estáticas del cold-start SÍ están (Capa 2 de la selección)
    for ancla in ("areayacimiento", "profundidad", "coordenadax", "coordenaday",
                  "well_age_months"):
        assert ancla in out.columns


def test_target_left_join():
    out = fsb.build_store_features(_panel_crudo()).sort_values(["idpozo", "periodo"])
    un_pozo = out[out.idpozo == 100].reset_index(drop=True)
    # y_next = prod_pet del mes siguiente; la última fila queda NULL (para inferencia)
    assert un_pozo["y_next"].iloc[:-1].notna().all()
    assert pd.isna(un_pozo["y_next"].iloc[-1])
    assert un_pozo["y_next"].iloc[0] == un_pozo["prod_pet"].iloc[1]


def test_table_for_mapea_petroleo_y_gas():
    # petróleo mantiene el nombre histórico; gas lleva sufijo (convención _gas, ADR-042)
    assert fsb.table_for("prod_pet") == "feat_produccion_pozo_mensual"
    assert fsb.table_for("prod_gas") == "feat_produccion_pozo_mensual_gas"


def test_build_gas_usa_features_de_gas_y_su_target():
    # El modelo de gas materializa las autorregresivas sobre prod_gas y su y_next.
    out = fsb.build_store_features(_panel_crudo(), target="prod_gas")
    esperadas = (
        ["idpozo", "periodo", "periodo_objetivo"]
        + mlf.selected_features("prod_gas") + ["y_next"]
    )
    assert list(out.columns) == esperadas
    # los nombres autorregresivos llevan el prefijo del target gas
    assert "prod_gas_roll3" in out.columns and "prod_pet_roll3" not in out.columns
    un_pozo = out[out.idpozo == 100].sort_values("periodo").reset_index(drop=True)
    # y_next = prod_gas del mes siguiente (en el panel prod_gas es constante = 5)
    assert un_pozo["y_next"].iloc[0] == un_pozo["prod_gas"].iloc[1]
    assert pd.isna(un_pozo["y_next"].iloc[-1])
