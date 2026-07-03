from datetime import date, timedelta
from app.core.demo_data import DECLINE_RATE_BBL_PER_DAY, get_well

# Horizonte máximo del forecast, en días. Como las fechas son arbitrarias, sin un tope
# un rango enorme (p. ej. date_end en el año 3000) generaría cientos de miles de pasos
# diarios y una respuesta gigante (riesgo de performance/DoS), además de carecer de
# sentido físico. La ruta valida contra este límite y devuelve 422. Es un parámetro de
# producto: ajustar acá para cambiar cuán lejos puede pedirse el forecast.
MAX_FORECAST_DAYS = 366  # ~1 año


def get_forecast(id_well: str, date_start: date, date_end: date) -> list[dict]:
    """Generate a daily production forecast using a linear decline model.

    El rango (date_end - date_start) se asume ya acotado por la ruta a
    ``MAX_FORECAST_DAYS`` días; el loop hace un paso por día del rango.
    """
    base = get_well(id_well)["base_production"]
    result = []
    current = date_start
    day = 0
    while current <= date_end:
        result.append({"date": current, "prod": round(base - day * DECLINE_RATE_BBL_PER_DAY, 2)})
        current += timedelta(days=1)
        day += 1
    return result
