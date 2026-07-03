"""Entrenamiento y evaluación del modelo de forecast de ``prod_pet`` (t+1) — Rol 1.3.

Flujo: **leer features → entrenar modelo → evaluar resultados** en val,
respetando el split temporal (ADR-028: se entrena con el pasado y se valida con el
futuro). Permite probar distintos algoritmos e hiperparámetros (con/sin tuning de
**CV temporal**, ADR-034) y guarda el ``Pipeline`` entrenado en disco.

Todo el preprocesamiento (imputación de NaN por feature + flags, one-hot, escalado;
sin clip ni log1p) vive en el ``Pipeline`` y se ajusta **solo con train / el train de
cada fold** (ADR-039).

**Tracking en MLflow (1.4/1.5, Rol 3):** el logging de experimentos y el model registry
los enchufa el Rol 3 con ``--mlflow`` (delega en ``ml.registry.log_and_register``);
``train()``/``train_final()`` devuelven ``(pipeline, info)`` como gancho para no
reescribir el entrenamiento. Sin ``--mlflow`` el entrenamiento no toca MLflow.

Uso:
    python -m ml.train                       # tunea y entrena el campeón (random_forest) y lo guarda
    python -m ml.train --model xgboost       # tunea/entrena otro modelo
    python -m ml.train --no-tune             # sin tuning: usa los mejores hiperparámetros registrados
    python -m ml.train --final               # entrena en dev (train+val) y evalúa en TEST (confirmación final)
    python -m ml.train --mlflow              # campeón final → loguea/registra/promueve en MLflow (Rol 3)
    python -m ml.train --mlflow --no-promote # ídem, pero deja la versión en Staging (sin promover)
    python -m ml.train --compare             # compara los 3 modelos sin tunear
    python -m ml.train --no-save
"""
from __future__ import annotations
import argparse
from pathlib import Path

import joblib

from ml import modeling
from ml.config import PROJECT_ROOT, TARGET, TARGETS

MODELS_DIR = PROJECT_ROOT / "models"
# Campeón según la comparación TUNEADA en val sobre el set recursion-safe (notebooks
# 05/06 §3.1, ADR-043/044): random_forest (val RMSE ~227.5 / R² 0.902 en petróleo;
# ~576.6 / 0.861 en gas) supera a xgboost, ridge y la persistencia. Se entrena siempre
# recursion-safe (ver train()), así que este campeón sirve para /predict y /forecast.
CHAMPION = "random_forest"
MODELOS = ("ridge", "random_forest", "xgboost")


def _untuned_estimator(name: str, target: str = TARGET):
    """Estimador sin tunear: usa los **mejores hiperparámetros registrados** para el
    ``target`` (``modeling.BEST_PARAMS[target]``, ADR-040), no defaults arbitrarios."""
    return modeling.make_estimator(name, target=target)


def train(model_name: str = CHAMPION, tune: bool = False, target: str = TARGET):
    """Entrena (con/sin tuning) y evalúa en val. Devuelve ``(pipeline, info)``.

    ``info`` trae el modelo, si fue tuneado, los mejores hiperparámetros y las
    métricas en train/val + la persistencia (baseline a batir, ADR-029). El
    ``pipeline`` devuelto es el artefacto a versionar/loguear (gancho para Rol 3).
    ``target`` elige petróleo (``prod_pet``) o gas (``prod_gas``, ADR-042).

    Entrena **siempre con el set recursion-safe** (ADR-043/044, notebooks 05/06): es el
    modelo por defecto y único de producción, apto tanto para ``/predict`` (un paso)
    como para el forecast recursivo (``/forecast``). Se excluyen las features que no se
    pueden recalcular en un mes futuro desde la trayectoria del target
    (``prod_vecinos_mean``, ``water_cut``, la producción cruzada, ``prod_agua``, ``tef``).
    """
    ds, feats = modeling.build_feature_matrix(target=target, recursion_safe=True)
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
            _untuned_estimator(model_name, target), model_name == "ridge", feats
        )
        pipe.fit(X_tr, y_tr)
        best_params = None

    info = {
        "model": model_name,
        "target": target,
        "tuned": tune,
        "best_params": best_params,
        "train": modeling.evaluate(y_tr, pipe.predict(X_tr)),
        "val": modeling.evaluate(y_va, pipe.predict(X_va)),
        "persistencia_val": modeling.persistence_val(ds, target),
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


def train_final(model_name: str = CHAMPION, params: dict | None = None, target: str = TARGET):
    """Entrena el modelo final en **dev (train+val)** con los mejores
    hiperparámetros registrados (``BEST_PARAMS``, ADR-040) y lo evalúa **una vez en
    test**. Es la confirmación final del campeón; no re-tunea. Devuelve ``(pipe, info)``.
    ``target`` elige petróleo (``prod_pet``) o gas (``prod_gas``, ADR-042).

    Usa **siempre el set recursion-safe** (ADR-043/044), igual que ``train``.
    """
    ds, feats = modeling.build_feature_matrix(target=target, recursion_safe=True)
    X_dev, y_dev, X_te, y_te = modeling.split_dev_test(ds, feats)
    pipe = modeling.build_pipeline(
        modeling.make_estimator(model_name, params, target=target), model_name == "ridge", feats
    )
    pipe.fit(X_dev, y_dev)
    return pipe, {
        "model": model_name,
        "target": target,
        "params": params or modeling.BEST_PARAMS[target][model_name],
        "n_dev": len(y_dev),
        "n_test": len(y_te),
        "dev": modeling.evaluate(y_dev, pipe.predict(X_dev)),
        "test": modeling.evaluate(y_te, pipe.predict(X_te)),
        "persistencia_test": modeling.evaluate(y_te, X_te[target]),
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


def compare(target: str = TARGET) -> None:
    """Compara los 3 modelos sin tunear sobre val (como la sección 3 del notebook).
    Sobre el set recursion-safe (ADR-043/044), igual que el resto del entrenamiento."""
    ds, feats = modeling.build_feature_matrix(target=target, recursion_safe=True)
    X_tr, y_tr, X_va, y_va = modeling.split_train_val(ds, feats)
    tabla, _ = modeling.train_eval_models(X_tr, y_tr, X_va, y_va)
    import pandas as pd
    pers = modeling.persistence_val(ds, target)
    tabla = pd.concat(
        [tabla, pd.DataFrame([{"modelo": "persistencia (baseline)", **pers}])],
        ignore_index=True,
    ).sort_values("val_rmse").reset_index(drop=True)
    print(tabla.round(3).to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Entrenamiento del forecast (Rol 1.3)")
    parser.add_argument("--model", choices=MODELOS, default=CHAMPION)
    parser.add_argument("--target", choices=TARGETS, default=TARGET,
                        help="Qué predecir: prod_pet (petróleo) o prod_gas (gas, ADR-042)")
    parser.add_argument("--no-tune", action="store_true",
                        help="No tunear: usa hiperparámetros por defecto (más rápido)")
    parser.add_argument("--compare", action="store_true", help="Comparar los 3 modelos sin tunear (no guarda)")
    parser.add_argument("--final", action="store_true",
                        help="Entrenar en dev (train+val) con los mejores hiperparámetros y evaluar en TEST")
    parser.add_argument("--mlflow", action="store_true",
                        help="Entrenar el campeón final y loguear/registrar/promover en MLflow (Rol 3, 1.4/1.5)")
    parser.add_argument("--no-promote", action="store_true",
                        help="Con --mlflow: registra la versión pero la deja en Staging (no promueve)")
    parser.add_argument("--no-save", action="store_true", help="No guardar el modelo entrenado")
    args = parser.parse_args()

    # sufijo de gas en el nombre del archivo para no pisar el modelo de petróleo
    # (petróleo mantiene su nombre histórico: random_forest.joblib, etc.). El modelo
    # es recursion-safe por defecto (ADR-043/044), así que no lleva sufijo extra: es
    # el único campeón de producción, usado por /predict y /forecast.
    suf = "" if args.target == "prod_pet" else f"_{args.target}"

    if args.compare:
        compare(args.target)
        return

    if args.mlflow:
        # Rol 3 (1.4/1.5): campeón final (dev=train+val, evaluado en test) → MLflow.
        # Import perezoso: solo se necesita mlflow cuando se usa este flag.
        from ml import registry
        pipe, info = train_final(args.model, target=args.target)
        _print_final(info)
        registry.log_and_register(pipe, info, promote=not args.no_promote)
        if not args.no_save:
            print(f"\n✓ Modelo guardado en {save_model(pipe, args.model + suf + '_final')}")
        return

    if args.final:
        pipe, info = train_final(args.model, target=args.target)
        _print_final(info)
        if not args.no_save:
            print(f"\n✓ Modelo guardado en {save_model(pipe, args.model + suf + '_final')}")
        return

    pipe, info = train(args.model, tune=not args.no_tune, target=args.target)
    _print_info(info)
    if not args.no_save:
        ruta = save_model(pipe, args.model + suf)
        print(f"\n✓ Modelo guardado en {ruta}")


if __name__ == "__main__":
    main()
