"""Carga los modelos de producción desde el MLflow Model Registry y los mantiene al día.

Hay **un modelo por target** (ADR-042): petróleo (`produccion-forecast`) y gas
(`produccion-forecast-gas`). Cada uno se maneja con su propio ``ModelLoader``, y el
módulo expone un registro ``MODEL_LOADERS`` indexado por target.

Patrón por modelo: singleton + polling en daemon thread.
- Al arrancar la API se llama a load(), que carga la versión en stage Production.
- start_polling() lanza un hilo que revisa cada POLL_INTERVAL segundos si hay una
  nueva versión en Production y, si la hay, la carga sin reiniciar el servidor
  (ADR-038, despliegue automático del modelo).
- Si MLflow no está disponible al arrancar, la API inicia igual; /predict retorna 503
  para el target cuyo modelo no esté cargado.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

import mlflow
import mlflow.sklearn
import pandas as pd
from mlflow.tracking import MlflowClient

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv("MODEL_POLL_INTERVAL_SECONDS", "300"))
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")

# Nombre del modelo registrado por target (debe coincidir con el registry del Rol 1:
# config.experiment_name(target)). Override por env var para entornos donde difiera.
MODEL_NAME_BY_TARGET = {
    "prod_pet": os.getenv("MLFLOW_MODEL_NAME", "produccion-forecast"),
    "prod_gas": os.getenv("MLFLOW_MODEL_NAME_GAS", "produccion-forecast-gas"),
}


class ModelLoader:
    """Carga y sirve un único modelo (un target) desde el MLflow registry."""

    def __init__(self, model_name: str, poll_interval: int = POLL_INTERVAL):
        self._model = None
        self._version: Optional[str] = None
        self._lock = threading.Lock()
        self._model_name = model_name
        self._poll_interval = poll_interval
        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)

    def load(self) -> None:
        """Carga (o recarga) la versión en stage Production desde el MLflow registry."""
        client = MlflowClient()
        versions = client.get_latest_versions(self._model_name, stages=["Production"])
        if not versions:
            raise RuntimeError(
                f"No hay ningún modelo en stage Production para '{self._model_name}'"
            )
        latest = versions[0]
        if latest.version == self._version:
            return
        model = mlflow.sklearn.load_model(f"models:/{self._model_name}/Production")
        with self._lock:
            self._model = model
            self._version = latest.version
        logger.info("Modelo '%s' versión %s cargado", self._model_name, self._version)

    def predict(self, features: pd.DataFrame) -> float:
        """Devuelve la predicción escalar para un DataFrame de features."""
        with self._lock:
            if self._model is None:
                raise RuntimeError(
                    f"Modelo '{self._model_name}' no disponible — MLflow inalcanzable "
                    f"o sin versión Production"
                )
            return float(self._model.predict(features)[0])

    @property
    def version(self) -> Optional[str]:
        return self._version

    @property
    def model_name(self) -> str:
        return self._model_name

    def start_polling(self) -> None:
        """Inicia un hilo daemon que recarga el modelo cuando se promueve una nueva versión."""
        def _poll():
            while True:
                time.sleep(self._poll_interval)
                try:
                    self.load()
                except Exception as exc:
                    logger.warning(
                        "Recarga de '%s' fallida: %s", self._model_name, exc
                    )

        thread = threading.Thread(
            target=_poll, daemon=True, name=f"model-reload-{self._model_name}"
        )
        thread.start()
        logger.info(
            "Polling de '%s' iniciado (intervalo=%ss)", self._model_name, self._poll_interval
        )


# Un loader por target. La API elige el loader según el target pedido en /predict.
MODEL_LOADERS: dict[str, ModelLoader] = {
    target: ModelLoader(model_name=name) for target, name in MODEL_NAME_BY_TARGET.items()
}


def get_loader(target: str) -> ModelLoader:
    """Devuelve el loader del target, o lanza ValueError si no está soportado."""
    loader = MODEL_LOADERS.get(target)
    if loader is None:
        raise ValueError(f"Target '{target}' no soportado. Opciones: {sorted(MODEL_LOADERS)}")
    return loader


def load_all() -> None:
    """Carga los modelos de todos los targets. Un fallo por target no frena a los otros
    (la API arranca en modo degradado y /predict de ese target retorna 503)."""
    for target, loader in MODEL_LOADERS.items():
        try:
            loader.load()
        except Exception as exc:
            logger.warning(
                "No se pudo cargar el modelo del target '%s' al arrancar: %s", target, exc
            )


def start_polling_all() -> None:
    """Arranca el polling de recarga automática para todos los targets."""
    for loader in MODEL_LOADERS.values():
        loader.start_polling()
