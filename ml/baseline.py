"""Baselines deterministas para el forecast de producción (ADR-029).

Calcula y evalúa las reglas:
- persistencia    : ŷ(t+1) = prod_pet(t)        <- baseline primario
- media_movil_3m  : ŷ(t+1) = media de {t, t-1, t-2}
- naive_estacional: ŷ(t+1) = prod_pet(t-11)     (mismo mes del año anterior)

Se calculan sobre el **mismo dataset que los modelos** (``build_basic_dataset``,
universo train-only + target por merge de calendario, ADR-031) y se evalúan en
**val** (comparación directa con los modelos del notebook 04) y en **test** (vara
de éxito del ADR-029). Cada baseline se loguea en MLflow como un run, con las
mismas métricas y el mismo split que los modelos.

Uso:
    python -m ml.baseline            # evalúa y loguea en MLflow
    python -m ml.baseline --no-mlflow
"""
from __future__ import annotations
import argparse

import mlflow
import numpy as np
from sklearn.metrics import mean_absolute_error, mean_squared_error

from ml import features
from ml.config import TARGET, TARGETS, experiment_name
from ml.dataset import build_basic_dataset
from ml.tracking import setup_mlflow

# nombre legible -> columna de predicción en el dataframe
BASELINES = {
    "persistencia": "b_persist",
    "media_movil_3m": "b_ma3",
    "naive_estacional": "b_seas",
}
PRIMARY = "persistencia"
SPLITS = ("val", "test")


def _rmse(y_true, y_pred) -> float:
    """RMSE compatible con cualquier versión de sklearn (sin
    ``root_mean_squared_error``, que recién existe desde 1.4)."""
    return float(np.sqrt(mean_squared_error(y_true, y_pred)))


def add_basic_baselines(ds, target=TARGET):
    """Agrega las columnas de predicción de los baselines sobre el dataset básico.

    ``target``(t) ya es la producción del mes t (persistencia) y ``{target}_roll3``
    es la media de {t, t-1, t-2} (media móvil 3m); el estacional se arma con el lag
    de calendario de 11 meses (mismo mes del año anterior respecto del target t+1).
    Todas usan solo información hasta el mes t (anti-leakage). Sirve para petróleo
    (``prod_pet``) y gas (``prod_gas``, ADR-039).
    """
    ds = ds.copy()
    ds["b_persist"] = ds[target]
    ds["b_ma3"] = ds[f"{target}_roll3"]
    ds["b_seas"] = features._calendar_lag(ds, target, 11)
    return ds


def evaluate_baselines(target: str = TARGET) -> dict[str, dict]:
    """Evalúa cada baseline en val y test. Devuelve
    ``{split: {nombre: {mae, rmse, n}}}``.

    Los splits **sin filas** se **omiten** (no quedan en el dict): pasa al backfillear
    con ``RETRAIN_ASOF`` una fecha anterior a ``VAL_END`` (ADR-040) — no se puede
    evaluar un período que todavía no existe a esa fecha. Igual a nivel baseline: si
    no hay filas usables, queda ``n=0`` con métricas ``nan`` (sin reventar)."""
    ds = add_basic_baselines(build_basic_dataset(target=target), target=target)
    results: dict[str, dict] = {}
    for split in SPLITS:
        s = ds[(ds.split == split) & ds.y_next.notna()]
        if s.empty:
            continue  # asof recorta antes de este split: se omite
        results[split] = {}
        for name, col in BASELINES.items():
            mask = s[col].notna()
            n = int(mask.sum())
            if n == 0:
                results[split][name] = {"mae": float("nan"), "rmse": float("nan"), "n": 0}
                continue
            y_true = s.loc[mask, "y_next"]
            y_pred = s.loc[mask, col]
            results[split][name] = {
                "mae": mean_absolute_error(y_true, y_pred),
                "rmse": _rmse(y_true, y_pred),
                "n": n,
            }
    return results


def _print_table(results: dict[str, dict]) -> None:
    for split in SPLITS:
        if split not in results:  # split omitido (sin datos a esa fecha de corte)
            print(f"\n[{split}]  (sin datos para este período — omitido por el corte asof)")
            continue
        print(f"\n[{split}]  {'Baseline':20s}{'MAE (m³)':>12s}{'RMSE (m³)':>12s}{'n':>10s}")
        print("-" * 60)
        r_split = results[split]
        for name, r in sorted(r_split.items(), key=lambda kv: kv[1]["rmse"], reverse=True):
            marca = "  <- primario" if name == PRIMARY else ""
            print(f"{'':6s}{name:20s}{r['mae']:>12.1f}{r['rmse']:>12.1f}{r['n']:>10,}{marca}")
    if "val" in results:
        prim = results["val"][PRIMARY]
        print(f"\nVara de éxito (ADR-029): el modelo debe superar la persistencia "
              f"(val RMSE = {prim['rmse']:.1f} m³, MAE = {prim['mae']:.1f} m³).")


def log_to_mlflow(results: dict[str, dict], target: str = TARGET) -> None:
    exp = experiment_name(target)
    setup_mlflow(exp)
    for name in BASELINES:
        with mlflow.start_run(run_name=f"baseline_{name}"):
            mlflow.set_tag("tipo", "baseline")
            mlflow.set_tag("target", target)
            mlflow.set_tag("primario", str(name == PRIMARY))
            mlflow.log_param("regla", name)
            mlflow.log_param("target", target)
            for split in SPLITS:
                if split not in results:  # split omitido por el corte asof
                    continue
                r = results[split][name]
                if r["n"] > 0:  # no loguear métricas nan de un split/baseline sin filas
                    mlflow.log_metric(f"{split}_mae", r["mae"])
                    mlflow.log_metric(f"{split}_rmse", r["rmse"])
                mlflow.log_metric(f"n_{split}", r["n"])
    print(f"\n✓ Baselines logueados en MLflow (experimento '{exp}').")


def main() -> None:
    parser = argparse.ArgumentParser(description="Baselines deterministas (ADR-029)")
    parser.add_argument("--target", choices=TARGETS, default=TARGET,
                        help="Qué predecir: prod_pet (petróleo) o prod_gas (gas, ADR-039)")
    parser.add_argument("--no-mlflow", action="store_true", help="No loguear en MLflow")
    args = parser.parse_args()

    results = evaluate_baselines(args.target)
    _print_table(results)
    if not args.no_mlflow:
        log_to_mlflow(results, args.target)


if __name__ == "__main__":
    main()
