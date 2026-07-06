"""Configuración central del pipeline de ML (Fase 3).

Las fechas de corte del split temporal salen del ADR-028 (Opción A), y el
universo y el target del mismo ADR. Centralizar acá evita que baseline.py y
train.py se desincronicen.
"""
import os
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# --- Datos ---
# Fuente provisoria: export de producción. Cuando el Rol 2 publique el feature
# store, este origen se reemplaza por una lectura del store (mismo contrato).
DATA_CSV = PROJECT_ROOT / "data" / "_explore" / "produccion_full.csv"

# Dataset básico ya procesado (features del mes t + target t+1). Es un derivado
# local del crudo: vive bajo data/ (gitignoreado por data/.gitignore) para no
# versionarlo ni tocar el original.
DATASET_BASICO_CSV = PROJECT_ROOT / "data" / "processed" / "dataset_basico.csv"

# Target por defecto: petróleo (ADR-028). El segundo modelo (ADR-039) pasa
# "prod_gas" como target; el pipeline está parametrizado para ambos.
TARGET = "prod_pet"  # m³ de petróleo (ADR-028)
TARGETS = ("prod_pet", "prod_gas")  # targets soportados (petróleo / gas, ADR-039)

# --- Reproducibilidad ---
# Semilla única para todo lo aleatorio del modelado (modelos, muestreo de
# hiperparámetros en el random search). Fijarla acá garantiza que las corridas
# sean reproducibles; se puede pisar por env var sin tocar código.
RANDOM_STATE = int(os.getenv("ML_RANDOM_STATE", "42"))

# --- Split temporal de 3 vías (ADR-028, Opción A) ---
# train: periodo <= TRAIN_END
# val:   TRAIN_END < periodo <= VAL_END
# test:  periodo > VAL_END
TRAIN_END = pd.Timestamp("2023-07-01")
VAL_END = pd.Timestamp("2024-11-01")


# --- Reproceso por fecha (retrain "como si fuera el día X", ADR-040) ---
def retrain_asof() -> pd.Timestamp | None:
    """Fecha de corte del reproceso, leída de la env var ``RETRAIN_ASOF``
    (``YYYY-MM-DD``), o ``None`` si no está seteada.

    El job de retrain la pasa para reentrenar **"como si fuera el día X"** (ADR-040,
    handoff de Rol 2): el dataset se recorta a ``periodo <= asof`` para **no usar
    datos posteriores** a esa fecha (anti-leakage al backfillear fechas pasadas). En
    una corrida normal (sin la env var) no recorta nada."""
    raw = os.getenv("RETRAIN_ASOF")
    return pd.Timestamp(raw) if raw else None

# --- MLflow ---
# Backend SQLite para el tracking local (el file store quedó deprecado en 2026).
# Los artefactos (modelos) van a ./mlartifacts. La infra "de verdad" de MLflow
# (docker-compose) es responsabilidad del Rol 3; acá usamos tracking local.
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", f"sqlite:///{PROJECT_ROOT / 'mlflow.db'}")
MLFLOW_ARTIFACT_URI = f"file:{PROJECT_ROOT / 'mlartifacts'}"
EXPERIMENT_NAME = "produccion-forecast"


def experiment_name(target: str = TARGET) -> str:
    """Experimento MLflow según el target: petróleo usa el experimento histórico
    (``produccion-forecast``) y gas uno propio (``produccion-forecast-gas``), para
    no mezclar los runs de los dos modelos (ADR-039)."""
    return EXPERIMENT_NAME if target == "prod_pet" else f"{EXPERIMENT_NAME}-gas"
