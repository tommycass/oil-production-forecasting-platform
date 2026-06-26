"""Configuración central del pipeline de ML (Fase 3).

Las fechas de corte del split temporal salen del ADR-028 (Opción A), y el
universo y el target del mismo ADR. Centralizar acá evita que baseline.py y
train.py se desincronicen.
"""
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# --- Datos ---
# Fuente provisoria: export de producción. Cuando el Rol 2 publique el feature
# store, este origen se reemplaza por una lectura del store (mismo contrato).
DATA_CSV = PROJECT_ROOT / "data" / "_explore" / "produccion_full.csv"

TARGET = "prod_pet"  # m³ de petróleo (ADR-028)

# --- Split temporal de 3 vías (ADR-028, Opción A) ---
# train: periodo <= TRAIN_END
# val:   TRAIN_END < periodo <= VAL_END
# test:  periodo > VAL_END
TRAIN_END = pd.Timestamp("2023-07-01")
VAL_END = pd.Timestamp("2024-11-01")

# --- MLflow ---
# Backend SQLite para el tracking local (el file store quedó deprecado en 2026).
# Los artefactos (modelos) van a ./mlartifacts. La infra "de verdad" de MLflow
# (docker-compose) es responsabilidad del Rol 3; acá usamos tracking local.
MLFLOW_TRACKING_URI = f"sqlite:///{PROJECT_ROOT / 'mlflow.db'}"
MLFLOW_ARTIFACT_URI = f"file:{PROJECT_ROOT / 'mlartifacts'}"
EXPERIMENT_NAME = "produccion-forecast"
