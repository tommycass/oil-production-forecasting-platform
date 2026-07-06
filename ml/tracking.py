"""Setup de MLflow compartido por baseline.py y train.py."""
from __future__ import annotations
import mlflow

from ml.config import MLFLOW_TRACKING_URI, MLFLOW_ARTIFACT_URI, EXPERIMENT_NAME


def setup_mlflow(experiment: str = EXPERIMENT_NAME) -> None:
    """Apunta MLflow al backend configurado y asegura el ``experiment`` creado.
    Idempotente. El modelo de gas usa su propio experimento
    (``config.experiment_name("prod_gas")``, ADR-039) para no mezclar runs.

    Ubicación de artefactos según el backend (ADR-036):
    - **Server remoto (``http(s)://``):** NO se fija ``artifact_location``. El server
      gestiona los artefactos y los sirve por HTTP (``--serve-artifacts``); el cliente
      (training) los sube y la API los descarga por red, sin acoplarse a una ruta del
      host. Es el flujo de docker/local con el perfil ``ml`` y el de staging/prod.
    - **Backend local (``sqlite``/``file``):** se fija ``artifact_location`` a la ruta
      local ``mlartifacts/`` (flujo de desarrollo individual de Rol 1, sin server)."""
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    if mlflow.get_experiment_by_name(experiment) is None:
        if MLFLOW_TRACKING_URI.startswith("http"):
            mlflow.create_experiment(experiment)
        else:
            mlflow.create_experiment(experiment, artifact_location=MLFLOW_ARTIFACT_URI)
    mlflow.set_experiment(experiment)
