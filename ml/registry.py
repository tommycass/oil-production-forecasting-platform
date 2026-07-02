"""Experiment tracking (1.4) y model registry (1.5) del forecast — Rol 3.

Enchufa MLflow **alrededor** del entrenamiento del Rol 1: `ml/train.py` deja el gancho
`(pipe, info)` (entrena y devuelve el Pipeline + las métricas) y este módulo se encarga
de **loguear la corrida** (params, métricas, versión de datos, modelo), **registrar** el
modelo en el registry —un modelo por target, `produccion-forecast` / `produccion-forecast-gas`
(ADR-042)— y **automatizar la promoción a Production** con el criterio del ADR-040.

Criterio de promoción (ADR-040), por target:
  el candidato pasa a Production si (a) **supera a la persistencia** en test RMSE
  (vara de éxito, ADR-029) y (b) **mejora al Production actual** de ese target (o no
  hay ninguno todavía). Si no, queda en Staging para inspección manual.

La fuente de verdad del logging/promoción es este módulo; se invoca desde
`python -m ml.train --mlflow --target <target>` (el seam del retrain, ADR-041:
`RETRAIN_CMD="python -m ml.train --mlflow"`).
"""
from __future__ import annotations

import hashlib
import logging

import mlflow
import mlflow.sklearn
from mlflow.tracking import MlflowClient

from ml.config import (
    DATA_CSV,
    RANDOM_STATE,
    TARGET,
    TRAIN_END,
    VAL_END,
    experiment_name,
    retrain_asof,
)
from ml.tracking import setup_mlflow

logger = logging.getLogger(__name__)

PRODUCTION = "Production"
STAGING = "Staging"


def promotion_decision(
    nuevo_test_rmse: float,
    persistencia_test_rmse: float,
    prod_test_rmse: float | None,
) -> tuple[bool, str]:
    """Decide si el candidato se promueve a Production (criterio ADR-040). **Función
    pura** (sin MLflow) para poder testearla en aislamiento.

    Devuelve ``(promover, motivo)``:
    - No promueve si **no supera la persistencia** (no cumple la vara de éxito, ADR-029).
    - Promueve si **no hay Production actual** (primer campeón del target).
    - Con Production actual: promueve solo si **mejora su test RMSE**.
    """
    if not (nuevo_test_rmse < persistencia_test_rmse):
        return False, (
            f"no supera la persistencia (test RMSE {nuevo_test_rmse:.2f} "
            f">= persistencia {persistencia_test_rmse:.2f})"
        )
    if prod_test_rmse is None:
        return True, "no hay modelo en Production; se promueve el primer campeón"
    if nuevo_test_rmse < prod_test_rmse:
        return True, (
            f"mejora al Production actual (test RMSE {nuevo_test_rmse:.2f} "
            f"< {prod_test_rmse:.2f})"
        )
    return False, (
        f"no mejora al Production actual (test RMSE {nuevo_test_rmse:.2f} "
        f">= {prod_test_rmse:.2f})"
    )


def _data_version(info: dict) -> str:
    """Fingerprint corto y determinista del corte de datos usado. Identifica el slice
    (fuente + split fijo del ADR-028 + asof del reproceso + tamaños), útil para
    distinguir un run de backfill de uno normal y para reproducibilidad."""
    asof = retrain_asof()
    firma = "|".join(
        str(x) for x in (DATA_CSV.name, TRAIN_END.date(), VAL_END.date(), asof,
                          info["n_dev"], info["n_test"])
    )
    return hashlib.sha1(firma.encode()).hexdigest()[:12]


def _current_production_rmse(client: MlflowClient, model_name: str) -> float | None:
    """test RMSE del Production actual de ese modelo (guardado como tag de la versión),
    o None si no hay Production o el modelo aún no existe en el registry."""
    try:
        prod = client.get_latest_versions(model_name, stages=[PRODUCTION])
    except Exception:  # el registered model no existe todavía
        return None
    if not prod:
        return None
    tag = prod[0].tags.get("test_rmse")
    return float(tag) if tag is not None else None


def log_and_register(pipe, info: dict, promote: bool = True) -> dict:
    """Loguea la corrida final en MLflow, registra el modelo por target y (si corresponde)
    lo promueve a Production.

    ``info`` es lo que devuelve ``train.train_final`` (entrenado en dev = train+val y
    evaluado en test): ``{model, target, params, n_dev, n_test, dev, test,
    persistencia_test}``. Devuelve un resumen ``{model_name, version, stage, promoted,
    motivo, test_rmse}``.
    """
    target = info.get("target", TARGET)
    model_name = experiment_name(target)  # produccion-forecast / produccion-forecast-gas
    setup_mlflow(model_name)

    test_rmse = info["test"]["val_rmse"]
    persistencia_rmse = info["persistencia_test"]["val_rmse"]
    data_ver = _data_version(info)
    asof = retrain_asof()

    with mlflow.start_run(run_name=f"{info['model']}_final_{target}") as run:
        mlflow.set_tag("tipo", "champion")
        mlflow.set_tag("modelo", info["model"])
        mlflow.set_tag("target", target)
        mlflow.set_tag("params_source", "fijo")  # train_final usa BEST_PARAMS (ADR-040)

        # 1.4 — hiperparámetros del modelo (esquema plano y estable: las claves de
        # BEST_PARAMS[target][modelo]) + reproducibilidad.
        mlflow.log_params(info["params"])
        mlflow.log_param("random_state", RANDOM_STATE)

        # 1.4 — versión de los datos usados (para reproducibilidad y auditar backfills).
        mlflow.log_param("train_end", str(TRAIN_END.date()))
        mlflow.log_param("val_end", str(VAL_END.date()))
        mlflow.log_param("retrain_asof", str(asof.date()) if asof is not None else "none")
        mlflow.log_param("data_source", DATA_CSV.name)
        mlflow.log_param("data_version", data_ver)
        mlflow.log_param("n_dev", info["n_dev"])
        mlflow.log_param("n_test", info["n_test"])

        # 1.4 — métricas de evaluación (dev + test + baseline de referencia).
        mlflow.log_metric("dev_rmse", info["dev"]["val_rmse"])
        mlflow.log_metric("dev_r2", info["dev"]["val_r2"])
        mlflow.log_metric("test_rmse", test_rmse)
        mlflow.log_metric("test_r2", info["test"]["val_r2"])
        mlflow.log_metric("persistencia_test_rmse", persistencia_rmse)

        # 1.4 — modelo entrenado (Pipeline completo: preprocesamiento + estimador).
        mlflow.sklearn.log_model(pipe, "model")
        model_uri = f"runs:/{run.info.run_id}/model"

    # 1.5 — registrar como nueva versión del modelo del target y versionar (v1, v2, …).
    client = MlflowClient()
    mv = mlflow.register_model(model_uri, model_name)
    version = mv.version

    # 2.1 — hiperparámetros y métricas atados a la VERSIÓN (no solo al run): así
    # get_model_version(n) responde "con qué params y qué RMSE se registró" sin mirar
    # el código ni navegar al run.
    client.set_model_version_tag(model_name, version, "test_rmse", str(test_rmse))
    client.set_model_version_tag(model_name, version, "params_source", "fijo")
    client.set_model_version_tag(model_name, version, "random_state", str(RANDOM_STATE))
    for k, v in info["params"].items():
        client.set_model_version_tag(model_name, version, f"param.{k}", str(v))

    # 1.5 — promoción automática (criterio ADR-040).
    prod_rmse = _current_production_rmse(client, model_name)
    promover, motivo = promotion_decision(test_rmse, persistencia_rmse, prod_rmse)
    if promote and promover:
        client.transition_model_version_stage(
            model_name, version, stage=PRODUCTION, archive_existing_versions=True
        )
        stage = PRODUCTION
    else:
        client.transition_model_version_stage(model_name, version, stage=STAGING)
        stage = STAGING

    logger.info(
        "Registrado %s v%s -> %s (%s)", model_name, version, stage,
        motivo if (promover or not promote) else motivo,
    )
    print(
        f"\n✓ {model_name} v{version} registrado en stage {stage}.\n"
        f"  test RMSE {test_rmse:.2f} | persistencia {persistencia_rmse:.2f} | "
        f"Production previo {prod_rmse if prod_rmse is not None else '—'}\n"
        f"  promoción: {'sí' if (promote and promover) else 'no'} — {motivo}"
    )
    return {
        "model_name": model_name,
        "version": version,
        "stage": stage,
        "promoted": bool(promote and promover),
        "motivo": motivo,
        "test_rmse": test_rmse,
    }
