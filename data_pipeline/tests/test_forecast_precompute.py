"""Tests del precómputo del forecast (forecast_precompute, ADR-045).

`build_predicciones` corre el motor recursivo real (`ml.forecast`) con un pipeline
*stub* sobre un store sintético en memoria — sin DB ni MLflow. Reusa `ml/features`
(sklearn) → se saltea si sklearn no está disponible.
"""
import pandas as pd
import pytest

pytest.importorskip("sklearn")  # ml.forecast reusa ml.features (NearestNeighbors)

from data_pipeline.orchestration import forecast_precompute as fp  # noqa: E402


class _Stub:
    """Pipeline stub: declina 3% el último valor del target (como los tests del motor)."""

    feature_names_in_ = None

    def __init__(self, target="prod_pet"):
        self.target = target

    def predict(self, X):
        return [float(X[self.target].iloc[-1]) * 0.97]


def _store(n_pozos=3, meses=6, target="prod_pet") -> pd.DataFrame:
    """Tabla del store sintética con el contrato del set seleccionado (ADR-036/043)."""
    filas = []
    for p in range(n_pozos):
        for m in range(1, meses + 1):
            filas.append({
                "idpozo": 100 + p,
                "periodo": pd.Timestamp(f"2025-{m:02d}-01"),
                "periodo_objetivo": pd.Timestamp(f"2025-{m + 1:02d}-01"),
                target: 1000.0 - 10 * m + p,
                f"{target}_roll3": 990.0,
                "areayacimiento": "YAC", "profundidad": 2500.0,
                "coordenadax": 1.0 + p, "coordenaday": 2.0 + p,
                "well_age_months": m - 1,
                "y_next": None if m == meses else 1000.0 - 10 * (m + 1) + p,
            })
    return pd.DataFrame(filas)


def test_pred_table_for_mapea_petroleo_y_gas():
    assert fp.pred_table_for("prod_pet") == "pred_produccion_pozo_mensual"
    assert fp.pred_table_for("prod_gas") == "pred_produccion_pozo_mensual_gas"


def test_build_predicciones_estructura_y_horizonte():
    out = fp.build_predicciones(_store(), _Stub(), "prod_pet", n_steps=12)
    assert list(out.columns) == ["idpozo", "periodo", "prediccion", "ultimo_observado"]
    assert len(out) == 3 * 12  # 12 meses por pozo
    un_pozo = out[out.idpozo == 100].sort_values("periodo").reset_index(drop=True)
    # arranca en ultimo_observado + 1 y avanza mes a mes, sin huecos
    assert un_pozo["ultimo_observado"].iloc[0] == pd.Timestamp("2025-06-01")
    assert un_pozo["periodo"].iloc[0] == pd.Timestamp("2025-07-01")
    assert list(un_pozo["periodo"]) == list(
        pd.date_range("2025-07-01", periods=12, freq="MS")
    )


def test_build_predicciones_declina_recursivamente():
    # El stub declina 3% por paso: la serie precomputada debe ser decreciente.
    out = fp.build_predicciones(_store(n_pozos=1), _Stub(), "prod_pet", n_steps=6)
    preds = list(out.sort_values("periodo")["prediccion"])
    assert preds == sorted(preds, reverse=True)
    assert preds[0] == pytest.approx((1000.0 - 60) * 0.97)  # último obs * factor


def test_build_predicciones_pozo_roto_no_voltea_el_batch(monkeypatch):
    # Si el forecast de UN pozo falla, los demás igual se precomputan.
    class Fragil(_Stub):
        def predict(self, X):
            if float(X["coordenadax"].iloc[-1]) == 1.0:  # pozo 100
                raise RuntimeError("pozo dañado")
            return super().predict(X)

    out = fp.build_predicciones(_store(n_pozos=3), Fragil(), "prod_pet", n_steps=3)
    assert set(out.idpozo) == {101, 102}


def test_precomputar_saltea_target_sin_modelo_production(monkeypatch):
    # Sin Production en el registry, el target se saltea (None) y no se toca la DB.
    monkeypatch.setattr(fp, "cargar_modelo_production", lambda target: None)
    assert fp.precomputar(engine=None, target="prod_pet") is None
    assert fp.precomputar_todos(engine=None) == {"prod_pet": None, "prod_gas": None}
