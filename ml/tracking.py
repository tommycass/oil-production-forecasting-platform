"""Setup de MLflow compartido por baseline.py y train.py."""
from __future__ import annotations
import mlflow

from ml.config import MLFLOW_TRACKING_URI, MLFLOW_ARTIFACT_URI, EXPERIMENT_NAME


def setup_mlflow() -> None:
    """Apunta MLflow al backend local y asegura el experimento (con su ubicación
    de artefactos) creado. Idempotente."""
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    if mlflow.get_experiment_by_name(EXPERIMENT_NAME) is None:
        mlflow.create_experiment(EXPERIMENT_NAME, artifact_location=MLFLOW_ARTIFACT_URI)
    mlflow.set_experiment(EXPERIMENT_NAME)
