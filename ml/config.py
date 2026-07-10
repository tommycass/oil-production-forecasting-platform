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
# FEATURE STORE como fuente de entrenamiento/evaluación. El
# training, la evaluación y los baselines leen la tabla del store por target (misma
# fuente que sirve la inferencia; cero training-serving skew, sin paths locales).
# El store se construye desde Bronze reusando el código de ml/ (paridad validada).
FEATURE_STORE_SCHEMA = "features"
FEATURE_STORE_TABLE = {
    "prod_pet": "feat_produccion_pozo_mensual",       # petróleo (nombre histórico)
    "prod_gas": "feat_produccion_pozo_mensual_gas",   # gas
}

# CSV crudo: SOLO referencia offline / validación de paridad del store (ver
# data_pipeline feature_store_build). NO es la fuente del entrenamiento (ese lee el
# store). Se mantiene para `build_basic_dataset` (assembly de referencia) y utilidades.
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

# --- Split temporal de 3 vías, derivado de la última fecha observada (ADR-028) ---
# Los cortes no se hardcodean: se derivan de max(periodo) con ventanas fijas, así el
# reproceso por fecha (asof) y los meses nuevos corren la ventana solos (walk-forward).
# 18/16 reproducen sobre el snapshot actual (max 2026-05) el split del ADR-028
# (train_end 2023-07, val_end 2024-11). Overridables por env.
TEST_MONTHS = int(os.getenv("ML_TEST_MONTHS", "18"))
VAL_MONTHS = int(os.getenv("ML_VAL_MONTHS", "16"))


def split_bounds(max_periodo) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Deriva ``(train_end, val_end)`` de la última fecha observada (ADR-028):
    ``train <= train_end < val <= val_end < test``. ``max_periodo`` debe incluir la
    fila de inferencia (``y_next`` NULL) para alinear universo del store y split."""
    max_periodo = pd.Timestamp(max_periodo)
    val_end = max_periodo - pd.DateOffset(months=TEST_MONTHS)
    train_end = val_end - pd.DateOffset(months=VAL_MONTHS)
    return train_end, val_end


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
