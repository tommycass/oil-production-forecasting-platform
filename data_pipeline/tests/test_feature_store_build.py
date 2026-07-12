"""Tests de la materialización del feature store (build_store_features).

La lógica reusa `ml/features` (sklearn) → se saltea si sklearn no está disponible.
Valida estructura y comportamiento clave (columnas = set FINAL de la selección
ADR-041, idpozo int, target left-join) sobre un panel sintético, sin red ni DB.
Cubre los **dos targets** (petróleo y gas, ADR-039) y el mapeo de tablas.
"""
import pandas as pd
import pytest

pytest.importorskip("sklearn")  # add_engineered_features usa NearestNeighbors

from data_pipeline.orchestration import feature_store_build as fsb  # noqa: E402
from ml import features as mlf  # noqa: E402
from ml.dataset import BASIC_CATEGORICAL_FEATURES  # noqa: E402


# El split se deriva de la última fecha (train = ~33 meses atrás, ADR-028); el panel debe
# abarcar más de TEST_MONTHS+VAL_MONTHS (17+16) para que el universo train-only no quede vacío.
_MESES = 40


def _panel_crudo(n_pozos=6, meses=_MESES, inicio="2018-01-01") -> pd.DataFrame:
    """Panel mensual sintético con TODAS las columnas crudas (como vendría de Bronze).

    Genera ``meses`` meses **consecutivos** por pozo desde ``inicio`` (cruza años), para
    que haya historia suficiente para el split temporal derivado (ADR-028)."""
    base = pd.Timestamp(inicio)
    filas = []
    for p in range(n_pozos):
        for k in range(meses):
            per = base + pd.DateOffset(months=k)
            fila = {
                "idpozo": str(100 + p), "anio": str(per.year), "mes": str(per.month),
                "prod_pet": str(10.0 + p + k), "prod_gas": "5", "prod_agua": "2",
                "tef": "30", "profundidad": "2500",
                "coordenadax": str(1.0 + p), "coordenaday": str(2.0 + p),
            }
            for c in BASIC_CATEGORICAL_FEATURES:
                fila[c] = f"{c}_v"
            filas.append(fila)
    return pd.DataFrame(filas)[fsb._RAW_COLS]


def test_columnas_y_grano():
    # El store materializa EXACTAMENTE el set final de la selección (ADR-041): la
    # misma lista que entrena ml/train.py (`selected_features`) + claves + y_next.
    out = fsb.build_store_features(_panel_crudo())
    esperadas = (
        ["idpozo", "periodo", "periodo_objetivo"]
        + mlf.selected_features("prod_pet") + ["y_next"]
    )
    assert list(out.columns) == esperadas
    assert str(out["idpozo"].dtype).startswith("int")
    assert len(out) == 6 * _MESES  # left-join + universo completo: conserva todas las filas


def test_solo_features_seleccionadas():
    # Solo se persisten las features de ganancia positiva (imp_mean > 0, ADR-041).
    # Las de imp. ≤ 0 NO entran: las no-recursion-safe, delta3 (ruido negativo) y las
    # categóricas de imp. ≤ 0.
    out = fsb.build_store_features(_panel_crudo())
    fuera = {"prod_vecinos_mean", "water_cut", "prod_agua", "tef", "prod_gas",
             "prod_pet_delta3", "provincia", "formacion", "formprod",
             "clasificacion", "tipoestado", "sub_tipo_recurso"}
    assert not fuera & set(out.columns)
    # las anclas estáticas del cold-start SÍ están (petróleo)
    for ancla in ("areayacimiento", "profundidad", "coordenadax", "coordenaday",
                  "well_age_months"):
        assert ancla in out.columns
    # y las features de ganancia positiva antes podadas ahora también (imp > 0)
    for feat in ("mes", "empresa", "tipopozo", "cuenca", "prod_pet_lag12",
                 "produjo_mes_pasado"):
        assert feat in out.columns


def test_target_left_join():
    out = fsb.build_store_features(_panel_crudo()).sort_values(["idpozo", "periodo"])
    un_pozo = out[out.idpozo == 100].reset_index(drop=True)
    # y_next = prod_pet del mes siguiente; la última fila queda NULL (para inferencia)
    assert un_pozo["y_next"].iloc[:-1].notna().all()
    assert pd.isna(un_pozo["y_next"].iloc[-1])
    assert un_pozo["y_next"].iloc[0] == un_pozo["prod_pet"].iloc[1]


def test_mes_es_mes_objetivo():
    # `mes` (feature del set, ADR-041) = mes del período OBJETIVO (t+1), igual que
    # ml/dataset.build_basic_dataset → sin skew si un consumidor lo lee del store.
    out = fsb.build_store_features(_panel_crudo())
    assert (out["mes"] == out["periodo_objetivo"].dt.month).all()


def test_table_for_mapea_petroleo_y_gas():
    # petróleo mantiene el nombre histórico; gas lleva sufijo (convención _gas, ADR-039)
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


def test_asof_recorta_datos_posteriores(monkeypatch):
    # Reproceso "como si fuera el día X" (ADR-040): asof recorta el crudo a
    # periodo <= asof ANTES de universo/features, para no usar datos posteriores al
    # reentrenar una fecha pasada (anti-leakage del backfill). El panel va de 2018-01 a
    # 2021-04; con asof=2021-01 no debe quedar ninguna fila posterior (y queda historia
    # suficiente para que el split derivado tenga train no vacío).
    monkeypatch.delenv("RETRAIN_ASOF", raising=False)
    panel = _panel_crudo(n_pozos=2)
    recortado = fsb.build_store_features(panel, asof="2021-01-01")
    assert recortado["periodo"].max() == pd.Timestamp("2021-01-01")

    # Sin asof, la corrida normal conserva hasta el último mes disponible.
    completo = fsb.build_store_features(panel)
    assert completo["periodo"].max() == pd.Timestamp("2021-04-01")


def test_es_backfill_distingue_reproceso_historico(monkeypatch):
    # es_backfill compara asof contra el último período observado en Bronze (ADR-040):
    # una fecha anterior = reproceso histórico que truncaría el store (→ tablas separadas).
    monkeypatch.setattr(fsb, "max_periodo_bronze", lambda _eng: pd.Timestamp("2026-05-01"))
    assert fsb.es_backfill(None, "2025-06-06") is True     # anterior → backfill
    assert fsb.es_backfill(None, "2026-07-06") is False    # posterior (hoy) → normal
    assert fsb.es_backfill(None, "2026-05-01") is False    # igual al último → normal


def test_dedup_conserva_el_registro_vigente():
    # Dos filas del mismo (idpozo, mes) por una rectificación: se conserva la vigente
    # (rectificado=true), igual criterio que silver_produccion_vigente.
    df = pd.DataFrame({
        "idpozo": [1, 1],
        "periodo": [pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-01")],
        "prod_pet": [100.0, 555.0],          # la vigente (rectificada) trae 555
        "rectificado": ["false", "true"],
        "fecha_data": ["2020-02-01", "2020-03-01"],
        "fecha_ingesta": ["2020-02-05", "2020-03-05"],
    })
    out = fsb._dedup_vigente(df)
    assert len(out) == 1 and out["prod_pet"].iloc[0] == 555.0
    assert not set(fsb._DEDUP_COLS) & set(out.columns)  # descarta las columnas de rectificación


def test_dedup_es_no_op_sin_columnas_de_rectificacion():
    # Los paneles sin columnas de rectificación (p. ej. un consumidor directo) pasan intactos.
    df = pd.DataFrame({"idpozo": [1, 2], "periodo": [pd.Timestamp("2020-01-01")] * 2, "prod_pet": [1.0, 2.0]})
    out = fsb._dedup_vigente(df)
    assert out.equals(df)


def test_asof_toma_env_var_si_no_es_explicito(monkeypatch):
    # asof=None cae en la env var RETRAIN_ASOF (mismo mecanismo que ml.config.retrain_asof),
    # así el subproceso de retrain la respeta sin pasarla explícita.
    monkeypatch.setenv("RETRAIN_ASOF", "2021-02-01")
    panel = _panel_crudo(n_pozos=2)
    out = fsb.build_store_features(panel)  # asof=None → lee env
    assert out["periodo"].max() == pd.Timestamp("2021-02-01")
