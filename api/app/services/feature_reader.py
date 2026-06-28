"""Lee features del feature store para inferencia en tiempo real.

Consulta la tabla features.feat_produccion_pozo_mensual con el mes base t
(= mes_objetivo - 1 mes) para obtener las features con las que predecir t+1.
"""

from app.core.database import fetch_all

# Columnas que el modelo ML espera — deben coincidir con las definidas en ml/dataset.py.
INFERENCE_FEATURES = ["lag1", "lag2", "lag3", "roll3", "antiguedad", "tef_lag1"]


def _prev_month(anio: int, mes: int) -> tuple[int, int]:
    if mes == 1:
        return anio - 1, 12
    return anio, mes - 1


def get_features_for_inference(idpozo: int, anio: int, mes: int) -> dict:
    """Retorna las features del mes t para predecir la producción del mes t+1 = (anio, mes).

    Args:
        idpozo: ID numérico del pozo.
        anio: Año del mes a PREDECIR (t+1).
        mes: Mes a predecir (1-12, t+1).

    Returns:
        Dict con las columnas INFERENCE_FEATURES para el mes base t.

    Raises:
        ValueError: Si el pozo no tiene datos en el mes base (pozo nuevo o sin historia).
    """
    anio_t, mes_t = _prev_month(anio, mes)

    sql = """
        SELECT lag1, lag2, lag3, roll3, antiguedad, tef_lag1
        FROM features.feat_produccion_pozo_mensual
        WHERE idpozo = :idpozo
          AND anio   = :anio
          AND mes    = :mes
        LIMIT 1
    """
    rows = fetch_all(sql, {"idpozo": idpozo, "anio": anio_t, "mes": mes_t})
    if not rows:
        raise ValueError(
            f"Pozo {idpozo} sin datos en {anio_t}-{mes_t:02d} "
            f"(mes base para predecir {anio}-{mes:02d})"
        )
    return rows[0]
