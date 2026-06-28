"""Entrenamiento y evaluación del modelo de forecast de ``prod_pet`` (t+1) — Rol 1.3.

Flujo: **leer features → entrenar modelo → evaluar resultados** en val,
respetando el split temporal (ADR-028: se entrena con el pasado y se valida con el
futuro). Permite probar distintos algoritmos e hiperparámetros (con/sin tuning de
**CV temporal**, ADR-034) y guarda el ``Pipeline`` entrenado en disco.

Todo el preprocesamiento (imputación/flags, log1p, clip, one-hot, escalado) vive en
el ``Pipeline`` y se ajusta **solo con train / el train de cada fold** (ADR-039).

**No loguea en MLflow:** el tracking de experimentos (1.4) y el model registry (1.5)
quedan a cargo del Rol 3. ``train()`` devuelve ``(pipeline, métricas)`` como gancho
para que ese logging se enchufe sin reescribir el entrenamiento.

Uso:
    python -m ml.train                       # tunea y entrena el campeón (random_forest) y lo guarda
    python -m ml.train --model xgboost       # tunea/entrena otro modelo
    python -m ml.train --no-tune             # sin tuning: usa los mejores hiperparámetros registrados
    python -m ml.train --final               # entrena en dev (train+val) y evalúa en TEST (confirmación final)
    python -m ml.train --compare             # compara los 3 modelos sin tunear
    python -m ml.train --no-save
"""
from __future__ import annotations
import argparse
from pathlib import Path

import joblib

from ml import modeling
from ml.config import PROJECT_ROOT

MODELS_DIR = PROJECT_ROOT / "models"
# Campeón según la comparación TUNEADA en val (notebook 03_modeling §4.1, ADR-040):
# random_forest (val RMSE 226.8 / R² 0.903) supera a xgboost, ridge y la persistencia.
# Ojo: xgboost ganaba SIN tunear, pero tuneado lo supera random_forest.
CHAMPION = "random_forest"
MODELOS = ("ridge", "random_forest", "xgboost")


def _untuned_estimator(name: str):
    """Estimador sin tunear: usa los **mejores hiperparámetros registrados**
    (``modeling.BEST_PARAMS``, ADR-040), no defaults arbitrarios."""
    return modeling.make_estimator(name)


def train(model_name: str = CHAMPION, tune: bool = False):
    """Entrena (con/sin tuning) y evalúa en val. Devuelve ``(pipeline, info)``.

    ``info`` trae el modelo, si fue tuneado, los mejores hiperparámetros y las
    métricas en train/val + la persistencia (baseline a batir, ADR-029). El
    ``pipeline`` devuelto es el artefacto a versionar/loguear (gancho para Rol 3).
    """
    ds, feats = modeling.build_feature_matrix()
    X_tr, y_tr, X_va, y_va = modeling.split_train_val(ds, feats)

    if tune:
        X_s, y_s, periodos = modeling.train_search_arrays(ds, feats)
        search = modeling.tune_model(
            modeling.search_spaces()[model_name], X_s, y_s, periodos, nombre=model_name
        )
        pipe = search.best_estimator_  # reentrenado en todo train
        best_params = search.best_params_
    else:
        pipe = modeling.build_pipeline(
            _untuned_estimator(model_name), model_name == "ridge", feats
        )
        pipe.fit(X_tr, y_tr)
        best_params = None

    info = {
        "model": model_name,
        "tuned": tune,
        "best_params": best_params,
        "train": modeling.evaluate(y_tr, pipe.predict(X_tr)),
        "val": modeling.evaluate(y_va, pipe.predict(X_va)),
        "persistencia_val": modeling.persistence_val(ds),
    }
    return pipe, info


def save_model(pipe, model_name: str, path: Path | None = None) -> Path:
    """Guarda el ``Pipeline`` entrenado con joblib (en ``models/``, gitignoreado)."""
    path = path or (MODELS_DIR / f"{model_name}.joblib")
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipe, path)
    return path


def _print_info(info: dict) -> None:
    v, t, p = info["val"], info["train"], info["persistencia_val"]
    print(f"\nModelo: {info['model']}  (tuneado={info['tuned']})")
    if info["best_params"]:
        print("  mejores hiperparámetros:", info["best_params"])
    print(f"  train : RMSE {t['val_rmse']:.2f}  R² {t['val_r2']:.3f}")
    print(f"  val   : RMSE {v['val_rmse']:.2f}  R² {v['val_r2']:.3f}")
    print(f"  persistencia (val): RMSE {p['val_rmse']:.2f}  R² {p['val_r2']:.3f}")
    gana = v["val_rmse"] < p["val_rmse"]
    print(f"  -> {'✓ supera' if gana else '✗ NO supera'} la persistencia en RMSE (val)")


def train_final(model_name: str = CHAMPION, params: dict | None = None):
    """Entrena el modelo final en **dev (train+val)** con los mejores
    hiperparámetros registrados (``BEST_PARAMS``, ADR-040) y lo evalúa **una vez en
    test**. Es la confirmación final del campeón; no re-tunea. Devuelve ``(pipe, info)``.
    """
    ds, feats = modeling.build_feature_matrix()
    X_dev, y_dev, X_te, y_te = modeling.split_dev_test(ds, feats)
    pipe = modeling.build_pipeline(
        modeling.make_estimator(model_name, params), model_name == "ridge", feats
    )
    pipe.fit(X_dev, y_dev)
    return pipe, {
        "model": model_name,
        "params": params or modeling.BEST_PARAMS[model_name],
        "n_dev": len(y_dev),
        "n_test": len(y_te),
        "dev": modeling.evaluate(y_dev, pipe.predict(X_dev)),
        "test": modeling.evaluate(y_te, pipe.predict(X_te)),
        "persistencia_test": modeling.evaluate(y_te, X_te["prod_pet"]),
    }


def _print_final(info: dict) -> None:
    d, te, p = info["dev"], info["test"], info["persistencia_test"]
    print(f"\nModelo final: {info['model']}  (entrenado en dev = train+val, n={info['n_dev']:,})")
    print("  hiperparámetros:", info["params"])
    print(f"  dev  : RMSE {d['val_rmse']:.2f}  R² {d['val_r2']:.3f}")
    print(f"  TEST : RMSE {te['val_rmse']:.2f}  R² {te['val_r2']:.3f}   (n={info['n_test']:,})")
    print(f"  persistencia (test): RMSE {p['val_rmse']:.2f}  R² {p['val_r2']:.3f}")
    gana = te["val_rmse"] < p["val_rmse"]
    print(f"  -> {'✓ supera' if gana else '✗ NO supera'} la persistencia en RMSE (test)")


def compare() -> None:
    """Compara los 3 modelos sin tunear sobre val (como la sección 3 del notebook)."""
    ds, feats = modeling.build_feature_matrix()
    X_tr, y_tr, X_va, y_va = modeling.split_train_val(ds, feats)
    tabla, _ = modeling.train_eval_models(X_tr, y_tr, X_va, y_va)
    import pandas as pd
    pers = modeling.persistence_val(ds)
    tabla = pd.concat(
        [tabla, pd.DataFrame([{"modelo": "persistencia (baseline)", **pers}])],
        ignore_index=True,
    ).sort_values("val_rmse").reset_index(drop=True)
    print(tabla.round(3).to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Entrenamiento del forecast (Rol 1.3)")
    parser.add_argument("--model", choices=MODELOS, default=CHAMPION)
    parser.add_argument("--no-tune", action="store_true",
                        help="No tunear: usa hiperparámetros por defecto (más rápido)")
    parser.add_argument("--compare", action="store_true", help="Comparar los 3 modelos sin tunear (no guarda)")
    parser.add_argument("--final", action="store_true",
                        help="Entrenar en dev (train+val) con los mejores hiperparámetros y evaluar en TEST")
    parser.add_argument("--no-save", action="store_true", help="No guardar el modelo entrenado")
    args = parser.parse_args()

    if args.compare:
        compare()
        return

    if args.final:
        pipe, info = train_final(args.model)
        _print_final(info)
        if not args.no_save:
            print(f"\n✓ Modelo guardado en {save_model(pipe, args.model + '_final')}")
        return

    pipe, info = train(args.model, tune=not args.no_tune)
    _print_info(info)
    if not args.no_save:
        ruta = save_model(pipe, args.model)
        print(f"\n✓ Modelo guardado en {ruta}")


if __name__ == "__main__":
    main()
