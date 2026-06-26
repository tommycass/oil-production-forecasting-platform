"""Baselines deterministas para el forecast de producción (ADR-029).

Calcula y evalúa en el conjunto de **test** (ADR-028) las reglas:
- persistencia    : ŷ(t+1) = y(t)            <- baseline primario
- media_movil_3m  : ŷ(t+1) = media últimos 3 meses
- naive_estacional: ŷ(t+1) = y(t-11)         (mismo mes del año anterior)

Cada baseline se loguea en MLflow como un run, con las mismas métricas y el
mismo split que tendrán los modelos, para comparación directa.

Uso:
    python -m ml.baseline            # evalúa y loguea en MLflow
    python -m ml.baseline --no-mlflow
"""
from __future__ import annotations
import argparse

import mlflow
from sklearn.metrics import mean_absolute_error, root_mean_squared_error

from ml.config import EXPERIMENT_NAME
from ml.dataset import build_modeling_frame
from ml.tracking import setup_mlflow

# nombre legible -> columna de predicción en el dataframe
BASELINES = {
    "persistencia": "b_persist",
    "media_movil_3m": "b_ma3",
    "naive_estacional": "b_seas",
}
PRIMARY = "persistencia"


def evaluate_baselines() -> dict[str, dict]:
    """Evalúa cada baseline en test. Devuelve {nombre: {mae, rmse, n}}."""
    df = build_modeling_frame()
    test = df[(df.split == "test") & df.y_next.notna()]

    results = {}
    for name, col in BASELINES.items():
        mask = test[col].notna()
        y_true = test.loc[mask, "y_next"]
        y_pred = test.loc[mask, col]
        results[name] = {
            "mae": mean_absolute_error(y_true, y_pred),
            "rmse": root_mean_squared_error(y_true, y_pred),
            "n": int(mask.sum()),
        }
    return results


def _print_table(results: dict[str, dict]) -> None:
    print(f"\n{'Baseline':20s}{'MAE (m³)':>12s}{'RMSE (m³)':>12s}{'n test':>10s}")
    print("-" * 54)
    for name, r in sorted(results.items(), key=lambda kv: kv[1]["mae"], reverse=True):
        marca = "  <- primario" if name == PRIMARY else ""
        print(f"{name:20s}{r['mae']:>12.1f}{r['rmse']:>12.1f}{r['n']:>10,}{marca}")
    print(f"\nUmbral de éxito (ADR-029): el modelo debe superar "
          f"MAE = {results[PRIMARY]['mae']:.1f} m³")


def log_to_mlflow(results: dict[str, dict]) -> None:
    setup_mlflow()
    for name, r in results.items():
        with mlflow.start_run(run_name=f"baseline_{name}"):
            mlflow.set_tag("tipo", "baseline")
            mlflow.set_tag("primario", str(name == PRIMARY))
            mlflow.log_param("regla", name)
            mlflow.log_metric("test_mae", r["mae"])
            mlflow.log_metric("test_rmse", r["rmse"])
            mlflow.log_metric("n_test", r["n"])
    print(f"\n✓ Baselines logueados en MLflow (experimento '{EXPERIMENT_NAME}').")


def main() -> None:
    parser = argparse.ArgumentParser(description="Baselines deterministas (ADR-029)")
    parser.add_argument("--no-mlflow", action="store_true", help="No loguear en MLflow")
    args = parser.parse_args()

    results = evaluate_baselines()
    _print_table(results)
    if not args.no_mlflow:
        log_to_mlflow(results)


if __name__ == "__main__":
    main()
