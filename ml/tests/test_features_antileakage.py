"""Tests del **argumento central del TP**: las features de ingeniería (`ml/features.py`) no
miran el futuro. El leakage temporal es el enemigo de toda la fase (ADR-028/031), así que se
fija con tests que un valor de un mes futuro **no altera** ninguna feature de un mes anterior,
y que los lags son **por calendario** (un hueco da NaN, no la fila anterior disponible).
"""
import numpy as np
import pandas as pd

from ml import features


def _panel(vals, pozo=1, inicio="2020-01-01") -> pd.DataFrame:
    """Panel mensual de un pozo: ``periodo`` = primer día del mes (freq MS), ``prod_pet`` = vals."""
    per = pd.date_range(inicio, periods=len(vals), freq="MS")
    return pd.DataFrame({"idpozo": pozo, "periodo": per, "prod_pet": list(vals)})


def test_features_no_miran_el_futuro():
    """Anti-leakage central: una feature en el mes t usa solo datos <= t. Cambiar la
    producción de un mes futuro no cambia las features de los meses anteriores."""
    vals = [10.0, 12.0, 15.0, 11.0, 20.0, 18.0]
    vals_fut = [*vals[:-1], 999.0]  # solo cambia el ÚLTIMO mes (futuro de todos los anteriores)
    for fn, col in [
        (features.add_prod_pet_roll3, "prod_pet_roll3"),
        (features.add_prod_pet_delta1, "prod_pet_delta1"),
        (features.add_prod_pet_lag2, "prod_pet_lag2"),
        (features.add_prod_pet_acum6, "prod_pet_acum6"),
    ]:
        base = fn(_panel(vals))[col].to_numpy()
        mod = fn(_panel(vals_fut))[col].to_numpy()
        # las features de los meses 0..n-2 no pueden cambiar (no ven el mes futuro)
        assert np.allclose(base[:-1], mod[:-1], equal_nan=True), f"{col} miró el futuro"


def test_roll3_es_media_de_t_t1_t2():
    """`roll3` en t = media de {t, t-1, t-2} (nivel reciente), no incluye t+1."""
    roll3 = features.add_prod_pet_roll3(_panel([10.0, 20.0, 30.0, 40.0]))["prod_pet_roll3"].to_numpy()
    assert roll3[3] == 30.0  # media(40, 30, 20)
    assert roll3[2] == 20.0  # media(30, 20, 10)


def test_delta1_es_t_menos_mes_anterior():
    """`delta1` en t = valor(t) - valor(t-1) (declinación mensual)."""
    delta1 = features.add_prod_pet_delta1(_panel([10.0, 25.0, 20.0]))["prod_pet_delta1"].to_numpy()
    assert delta1[1] == 15.0 and delta1[2] == -5.0


def test_lag_por_calendario_respeta_huecos():
    """El lag es por calendario, no por `shift` de filas: si falta un mes, el lag queda NaN —
    no trae la fila anterior disponible, que tendría el horizonte equivocado (leakage silencioso)."""
    per = [pd.Timestamp("2020-01-01"), pd.Timestamp("2020-02-01"), pd.Timestamp("2020-04-01")]  # falta marzo
    df = pd.DataFrame({"idpozo": 1, "periodo": per, "prod_pet": [10.0, 20.0, 40.0]})
    lag1 = features._calendar_lag(df, "prod_pet", 1)
    assert lag1[1] == 10.0  # febrero pide enero → 10
    assert np.isnan(lag1[2])  # abril pide marzo (no existe) → NaN, NO el 20 de febrero
