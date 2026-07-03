"""Servicio del endpoint ``/forecast``: **pronóstico recursivo mensual** (ADR-044).

Reemplaza el mock de declinación lineal de la Fase 1 por el pronóstico real:

1. Lee la **serie histórica** del pozo del feature store (``feature_reader``, seam Rol 2).
2. Calcula la **ventana futura** pedida, acotada al **horizonte máximo** (``MAX_FORECAST_MONTHS``).
3. Corre el **motor recursivo** (``ml.forecast``) reusando el modelo de MLflow.
4. Devuelve la serie **mensual** con la forma del contrato de Fase 1: ``[{date, prod}]``.

El contrato de ``/forecast`` (``id_well``, ``date_start``, ``date_end`` → ``{id_well,
data:[{date, prod}]}``) **no cambia**. Se agrega solo un parámetro **opcional** ``target``
(default ``prod_pet``): quien no lo pasa obtiene petróleo, igual que antes.

El paso unitario (predecir un mes) es el modelo que antes servía ``/predict``; ``/forecast``
lo **subsume** (un rango de un mes = la vieja predicción de un mes), así que ``/predict`` se
retira (ADR-035 queda reemplazado por ADR-044).
"""

from datetime import date

from app.services.feature_reader import get_history_for_forecast
from app.services.model_loader import get_loader

# Horizonte máximo del forecast, en **meses** (ADR-044). Con la salida mensual el tope pasa
# de días (mock) a meses. Además de evitar respuestas enormes, **acota la acumulación de
# error** del recursivo: cada paso se apoya en la predicción anterior, así que a más meses,
# menos confiable. Se mide desde el último mes con dato real del pozo. Si el rango pedido lo
# supera, se **recorta** hasta este tope (no se rechaza; se devuelven los meses hasta ahí).
MAX_FORECAST_MONTHS = 12


class ForecastRangeError(Exception):
    """El rango pedido no tiene meses pronosticables (todo pasado, o empieza más allá del
    horizonte máximo). La ruta lo traduce a 422."""


def _month_start(d: date) -> date:
    """Primer día del mes de ``d`` (el forecast es mensual)."""
    return date(d.year, d.month, 1)


def _add_months(d: date, n: int) -> date:
    """``d`` desplazado ``n`` meses (sobre el primer día del mes)."""
    total = (d.year * 12 + (d.month - 1)) + n
    return date(total // 12, total % 12 + 1, 1)


def _months_between(a: date, b: date) -> int:
    """Cantidad de meses de ``a`` a ``b`` (positivo si ``b`` es posterior)."""
    return (b.year - a.year) * 12 + (b.month - a.month)


def get_forecast(
    id_well: str, date_start: date, date_end: date, target: str = "prod_pet"
) -> list[dict]:
    """Pronóstico recursivo **mensual** de ``id_well`` entre ``date_start`` y ``date_end``.

    Devuelve una lista ``[{date, prod}]`` (forma del contrato): un punto por **mes futuro**,
    ``date`` = primer día del mes, hasta el horizonte máximo. Asume que la ruta ya validó
    ``date_start <= date_end`` y que el pozo existe en el DW.

    ``target`` elige petróleo (``prod_pet``, default) o gas (``prod_gas``).

    Raises:
        ValueError: el pozo no tiene serie en el feature store (fuera del universo) → 404.
        ForecastRangeError: el rango no tiene meses pronosticables → 422.
        NotImplementedError: falta el lector de historia (adaptación del feature store) → 503.
        RuntimeError: el modelo no está disponible en MLflow → 503.
    """
    idpozo = int(id_well)

    # 1) features del mes base + serie observada + estáticos (seam feature store). ValueError
    #    si el pozo no tiene historia; NotImplementedError mientras el lector no esté (Rol 2).
    base_features, series, static = get_history_for_forecast(idpozo, target)

    # 2) ventana futura, acotada al horizonte. L = último mes observado.
    ultimo_obs = _month_start(max(series["periodo"]))
    primer_futuro = _add_months(ultimo_obs, 1)
    tope = _add_months(ultimo_obs, MAX_FORECAST_MONTHS)  # último mes permitido

    inicio = max(_month_start(date_start), primer_futuro)  # solo futuro
    fin_pedido = _month_start(date_end)

    if fin_pedido < primer_futuro:
        raise ForecastRangeError(
            "El rango no incluye meses futuros para pronosticar "
            f"(último dato del pozo: {ultimo_obs.isoformat()})"
        )
    if inicio > tope:
        raise ForecastRangeError(
            f"El rango empieza más allá del horizonte máximo de {MAX_FORECAST_MONTHS} meses "
            f"desde el último dato ({ultimo_obs.isoformat()})"
        )

    fin_efectivo = min(fin_pedido, tope)  # recorte al horizonte (si aplica)
    n_steps = _months_between(ultimo_obs, fin_efectivo)  # meses de L+1 a fin_efectivo

    # 3) motor recursivo. Import perezoso: ml/ (pandas/sklearn) es pesado y puede no estar
    #    en un entorno mínimo → así el módulo carga igual y sin el modelo degrada a 503.
    from ml import forecast as engine

    loader = get_loader(target)  # RuntimeError -> 503 si no hay modelo cargado
    preds = engine.recursive_forecast(
        loader.snapshot_model(), base_features, series, static, target, n_steps
    )

    # 4) recortar a la ventana pedida [inicio, fin_efectivo] y dar la forma {date, prod}
    return [
        {"date": _month_start(p["periodo"]), "prod": round(p[target], 2)}
        for p in preds
        if inicio <= _month_start(p["periodo"]) <= fin_efectivo
    ]
