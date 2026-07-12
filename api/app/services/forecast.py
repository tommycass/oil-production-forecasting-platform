"""Servicio del endpoint ``/forecast``: **pronóstico mensual** (ADR-042/043).

Reemplaza el mock de declinación lineal de la Fase 1 por el pronóstico real, con dos
caminos que dan **el mismo resultado** (mismo modelo Production + mismas features):

1. **Precomputado (lookup, ADR-043):** el job de retrain deja el pronóstico de 12 meses
   de cada pozo en ``features.pred_produccion_pozo_mensual`` (``_gas``). Si el pozo tiene
   precómputo **fresco** (su ``ultimo_observado`` coincide con el último mes del store),
   se sirve directo de la tabla: sin modelo ni MLflow en el request.
2. **On-the-fly (fallback, ADR-042):** sin precómputo (o si quedó viejo respecto del
   store), se corre el **motor recursivo** (``ml.forecast``) con el modelo de MLflow,
   como siempre. La elección es **transparente** para el usuario.

El contrato de ``/forecast`` (``id_well``, ``date_start``, ``date_end`` → ``{id_well,
data:[{date, prod}]}``) **no cambia**. Se agrega solo un parámetro **opcional** ``target``
(default ``prod_pet``): quien no lo pasa obtiene petróleo, igual que antes.

El paso unitario (predecir un mes) es el modelo que antes servía ``/predict``; ``/forecast``
lo **subsume** (un rango de un mes = la vieja predicción de un mes), así que ``/predict`` se
retira (ADR-035 queda reemplazado por ADR-042).
"""

from datetime import date

from app.services.feature_reader import (
    get_history_for_forecast,
    get_precomputed_forecast,
    get_ultimo_periodo_observado,
)
from app.services.model_loader import get_loader

# Horizonte máximo del forecast, en **meses** (ADR-042). Además de evitar respuestas
# enormes, **acota la acumulación de error** del recursivo: cada paso se apoya en la
# predicción anterior, así que a más meses, menos confiable. Se mide desde el último mes
# con dato real del pozo. Si el rango pedido lo supera, se **recorta** hasta este tope.
# El precómputo (ADR-043) genera exactamente este mismo horizonte por pozo.
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


def _ventana(ultimo_obs: date, date_start: date, date_end: date) -> tuple[date, date]:
    """Ventana de meses a devolver ``[inicio, fin_efectivo]``: solo futuro (desde
    ``ultimo_obs + 1``) y acotada al horizonte máximo. Es la MISMA regla para el camino
    precomputado y el on-the-fly (por eso vive en un helper: la transparencia del
    precómputo depende de que ambos recorten idéntico).

    Raises:
        ForecastRangeError: rango sin meses futuros, o que empieza más allá del tope.
    """
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
    return inicio, min(fin_pedido, tope)


def _desde_precomputo(
    idpozo: int, date_start: date, date_end: date, target: str
) -> list[dict] | None:
    """Intenta servir el pronóstico desde la tabla precomputada (ADR-043).

    Devuelve la lista ``[{date, prod}]`` o ``None`` si no se puede (sin precómputo, o
    **stale**: el store tiene un mes más nuevo que el que vio el precómputo, o la ventana
    pedida no está completa en la tabla) → el caller cae al motor on-the-fly.

    Puede levantar ``ForecastRangeError`` (rango inválido): esa validación usa el mismo
    ``ultimo_observado`` que usaría el on-the-fly (frescura ya verificada), así que el
    422 es idéntico por cualquiera de los dos caminos.
    """
    filas = get_precomputed_forecast(idpozo, target)
    if not filas:
        return None

    ultimo_obs = _month_start(filas[0]["ultimo_observado"])
    ultimo_store = get_ultimo_periodo_observado(idpozo, target)
    if ultimo_store is None or _month_start(ultimo_store) != ultimo_obs:
        return None  # precómputo viejo (o pozo ya no está en el store) → recalcular

    inicio, fin_efectivo = _ventana(ultimo_obs, date_start, date_end)

    puntos = {
        _month_start(f["periodo"]): float(f["prediccion"])
        for f in filas
        if inicio <= _month_start(f["periodo"]) <= fin_efectivo
    }
    # Guarda de completitud: si falta algún mes de la ventana (p. ej. el precómputo se
    # generó con un horizonte menor), no se sirve parcial: se recalcula on-the-fly.
    if len(puntos) != _months_between(inicio, fin_efectivo) + 1:
        return None
    return [
        {"date": mes, "prod": round(puntos[mes], 2)} for mes in sorted(puntos)
    ]


def get_forecast(
    id_well: str, date_start: date, date_end: date, target: str = "prod_pet"
) -> list[dict]:
    """Pronóstico **mensual** de ``id_well`` entre ``date_start`` y ``date_end``.

    Devuelve una lista ``[{date, prod}]`` (forma del contrato): un punto por **mes futuro**,
    ``date`` = primer día del mes, hasta el horizonte máximo. Asume que la ruta ya validó
    ``date_start <= date_end`` y que el pozo existe en el DW.

    Sirve el **precómputo** del retrain si está fresco (lookup, sin tocar el modelo);
    si no, corre el **motor recursivo** con el modelo de MLflow (ADR-043: mismo
    resultado, transparente para el usuario).

    ``target`` elige petróleo (``prod_pet``, default) o gas (``prod_gas``).

    Raises:
        ValueError: el pozo no tiene serie en el feature store (fuera del universo) → 404.
        ForecastRangeError: el rango no tiene meses pronosticables → 422.
        RuntimeError: el modelo no está disponible en MLflow → 503 (solo puede pasar en
            el camino on-the-fly; con precómputo fresco no se toca MLflow).
    """
    idpozo = int(id_well)

    # 1) camino precomputado (ADR-043): lookup si hay precómputo fresco para el pozo.
    precomputado = _desde_precomputo(idpozo, date_start, date_end, target)
    if precomputado is not None:
        return precomputado

    # 2) fallback on-the-fly (ADR-042): features del mes base + serie observada +
    #    estáticos, leídos del feature store. ValueError si el pozo no tiene serie
    #    (fuera del universo / sin historia).
    base_features, series, static = get_history_for_forecast(idpozo, target)

    # ventana futura, acotada al horizonte. L = último mes observado.
    ultimo_obs = _month_start(max(series["periodo"]))
    inicio, fin_efectivo = _ventana(ultimo_obs, date_start, date_end)
    n_steps = _months_between(ultimo_obs, fin_efectivo)  # meses de L+1 a fin_efectivo

    # motor recursivo. Import perezoso: ml/ (pandas/sklearn) es pesado, así el módulo
    # carga rápido y solo se importa en el fallback on-the-fly. En el contenedor, ml/ se
    # monta como volumen (infra/docker-compose.yml, `../ml:/app/ml:ro`) para que sea
    # importable; la falta de modelo Production la maneja get_loader() abajo (→ 503).
    from ml import forecast as engine

    loader = get_loader(target)  # RuntimeError -> 503 si no hay modelo cargado
    preds = engine.recursive_forecast(
        loader.snapshot_model(), base_features, series, static, target, n_steps
    )

    # recortar a la ventana pedida [inicio, fin_efectivo] y dar la forma {date, prod}
    return [
        {"date": _month_start(p["periodo"]), "prod": round(p[target], 2)}
        for p in preds
        if inicio <= _month_start(p["periodo"]) <= fin_efectivo
    ]
