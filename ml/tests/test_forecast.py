"""Tests del motor de forecast recursivo (``ml/forecast.py``, ADR-042).

Prueban la mecánica pura con un modelo *stub*, sin pipeline entrenado ni base de datos:
- paso 1 usa las **features del mes base** (del store), sin recalcular;
- pasos futuros **recalculan** las features recursion-safe desde la serie;
- realimentación, piso, gas, y el caso de ``feature_names_in_`` como **ndarray**
  (regresión: ``array or []`` es ambiguo en numpy).
"""
import numpy as np
import pandas as pd
import pytest

from ml import forecast


def _series(n=6, start="2023-07-01", target="prod_pet", base=1000, step=20):
    return pd.DataFrame({
        "periodo": pd.date_range(start, periods=n, freq="MS"),
        target: [base - step * i for i in range(n)],
    })


_STATIC = {
    "idpozo": 7, "profundidad": 2000.0, "coordenadax": 1.0, "coordenaday": 2.0,
    "empresa": "ACME", "tipopozo": "Pet", "areayacimiento": "YAC",
}


def _base(series, target="prod_pet", **override):
    """Fila del mes base como la daría el store: último valor del target + estáticos."""
    row = {target: float(series[target].iloc[-1]), **_STATIC}
    row.update(override)
    return row


class _Stub:
    """Modelo stub: declina ``factor`` el último valor del target. ``cols`` fija
    ``feature_names_in_`` (None o ndarray, como un pipeline sklearn real)."""

    def __init__(self, target="prod_pet", factor=0.97, cols=None):
        self.target = target
        self.factor = factor
        self.feature_names_in_ = None if cols is None else np.array(cols)

    def predict(self, X):
        return [float(X[self.target].iloc[-1]) * self.factor]


def _run(stub, series, n, target="prod_pet", base=None):
    base = _base(series, target) if base is None else base
    return forecast.recursive_forecast(stub, base, series, _STATIC, target, n)


# --- _model_columns: el bug del ndarray -------------------------------------

def test_model_columns_ndarray_no_rompe():
    obj = _Stub(cols=["prod_pet", "profundidad"])
    assert forecast._model_columns(obj) == ["prod_pet", "profundidad"]


def test_model_columns_sin_atributo_es_none():
    class Sin:
        pass
    assert forecast._model_columns(Sin()) is None


# --- mecánica ---------------------------------------------------------------

def test_recursion_realimenta_y_declina():
    s = _series()  # último valor = 1000 - 20*5 = 900
    res = _run(_Stub(factor=0.97), s, 4)
    assert len(res) == 4
    assert res[0]["periodo"] == pd.Timestamp("2024-01-01")  # último obs (2023-12) + 1
    assert res[0]["prod_pet"] == pytest.approx(900 * 0.97)          # paso 1 (mes base)
    assert res[1]["prod_pet"] == pytest.approx(900 * 0.97 * 0.97)   # realimentado


def test_paso1_usa_base_features_no_la_serie():
    """El primer mes se predice con la fila del store (base_features), NO recalculando
    desde la serie. Se prueba con un base_features cuyo target difiere del último de la serie."""
    s = _series()  # último de la serie = 900
    base = _base(s)
    base["prod_pet"] = 500.0  # el store "dice" 500, distinto del 900 de la serie
    res = forecast.recursive_forecast(_Stub(factor=0.9), base, s, _STATIC, "prod_pet", 1)
    assert res[0]["prod_pet"] == pytest.approx(500 * 0.9)  # usó base_features, no la serie


def test_features_recursivas_se_calculan_en_pasos_futuros():
    """El paso 2 (recomputado) debe traer las features recursion-safe en el X."""
    seen = {}

    class Check(_Stub):
        def predict(self, X):
            seen.update({c: True for c in X.columns})
            return super().predict(X)

    _run(Check(), _series(), 2)  # n=2 → hay al menos un paso recomputado
    for col in ("prod_pet_roll3", "prod_pet_cummax", "well_age_months", "mes"):
        assert col in seen


def test_columnas_del_modelo_ndarray_end_to_end():
    cols = ["prod_pet", "prod_pet_roll3", "profundidad"]
    res = _run(_Stub(cols=cols), _series(), 3)
    assert len(res) == 3


def test_piso_no_negativo():
    res = _run(_Stub(factor=0.1), _series(base=10, step=8), 6)
    assert all(r["prod_pet"] >= 0 for r in res)


def test_gas_target():
    s = _series(target="prod_gas")
    res = _run(_Stub(target="prod_gas"), s, 2, target="prod_gas")
    assert len(res) == 2 and all("prod_gas" in r for r in res)


def test_series_vacia_lanza():
    empty = pd.DataFrame({"periodo": [], "prod_pet": []})
    with pytest.raises(ValueError):
        forecast.recursive_forecast(_Stub(), {"prod_pet": 1.0}, empty, _STATIC, "prod_pet", 3)


def test_n_steps_cero_da_vacio():
    assert _run(_Stub(), _series(), 0) == []


def test_latencia_12_pasos_es_holgada():
    """Guarda de regresión del RNF de latencia (/forecast < 5 s). Mide solo el costo del
    motor (recompute de features × pasos) con un modelo stub instantáneo, sobre una serie
    larga (peor caso). Umbral holgado para no ser flaky por hardware; el modelo real suma
    ~13 ms/paso extra (medido: 12 pasos ≈ 0,6 s end-to-end)."""
    import time

    serie_larga = _series(n=240)  # ~20 años de historia (peor caso para el recompute)
    base = _base(serie_larga)
    t0 = time.perf_counter()
    forecast.recursive_forecast(_Stub(), base, serie_larga, _STATIC, "prod_pet", 12)
    assert time.perf_counter() - t0 < 3.0  # >5x margen sobre lo medido
