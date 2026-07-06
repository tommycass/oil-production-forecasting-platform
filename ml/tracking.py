"""Setup de MLflow compartido por baseline.py y train.py."""
from __future__ import annotations
import mlflow

from ml.config import MLFLOW_TRACKING_URI, MLFLOW_ARTIFACT_URI, EXPERIMENT_NAME


def setup_mlflow(experiment: str = EXPERIMENT_NAME) -> None:
    """Apunta MLflow al backend local y asegura el ``experiment`` (con su ubicación
    de artefactos) creado. Idempotente. El modelo de gas usa su propio experimento
    (``config.experiment_name("prod_gas")``, ADR-039) para no mezclar runs."""
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    if mlflow.get_experiment_by_name(experiment) is None:
        mlflow.create_experiment(experiment, artifact_location=MLFLOW_ARTIFACT_URI)
    mlflow.set_experiment(experiment)
