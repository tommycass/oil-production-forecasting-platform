"""Carga el modelo de producción desde el MLflow Model Registry y lo mantiene actualizado.

Patrón singleton + polling en daemon thread:
- Al arrancar la API se llama a load(), que carga el modelo en stage Production.
- start_polling() lanza un hilo que revisa cada POLL_INTERVAL segundos si hay una
  nueva versión en Production y, si la hay, la carga sin reiniciar el servidor.
- Si MLflow no está disponible al arrancar, la API inicia de todas formas; /predict
  retorna 503 hasta que el modelo esté disponible.
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

MODEL_NAME = os.getenv("MLFLOW_MODEL_NAME", "produccion-forecast")
POLL_INTERVAL = int(os.getenv("MODEL_POLL_INTERVAL_SECONDS", "300"))
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")


class ModelLoader:
    def __init__(self, model_name: str, poll_interval: int = POLL_INTERVAL):
        self._model = None
        self._version: Optional[str] = None
        self._lock = threading.Lock()
        self._model_name = model_name
        self._poll_interval = poll_interval
        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)

    def load(self) -> None:
        """Carga (o recarga) el modelo en stage Production desde el MLflow registry."""
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
                    "Modelo no disponible — MLflow inalcanzable o sin versión Production"
                )
            return float(self._model.predict(features)[0])

    @property
    def version(self) -> Optional[str]:
        return self._version

    def start_polling(self) -> None:
        """Inicia un hilo daemon que recarga el modelo cuando se promueve una nueva versión."""
        def _poll():
            while True:
                time.sleep(self._poll_interval)
                try:
                    self.load()
                except Exception as exc:
                    logger.warning("Recarga de modelo fallida: %s", exc)

        thread = threading.Thread(target=_poll, daemon=True, name="model-reload-poller")
        thread.start()
        logger.info("Polling de modelo iniciado (intervalo=%ss)", self._poll_interval)


MODEL_LOADER = ModelLoader(model_name=MODEL_NAME)
