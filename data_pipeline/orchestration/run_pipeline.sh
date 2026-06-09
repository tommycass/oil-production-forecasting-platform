#!/usr/bin/env bash
# Refresh recurrente del DW, headless (para cron). Recarga TODO el Bronze de
# producción desde el landing (full reload: atrapa altas y correcciones de
# cualquier antigüedad vía `rectificado`/`fecha_data`, no solo meses recientes),
# lo carga a Postgres y corre dbt (Silver/Gold/Data Quality). Env-driven: el mismo
# script sirve a staging y prod según el infra/.env de cada EC2. Ver ADR-021.
#
# Uso:
#   data_pipeline/orchestration/run_pipeline.sh
#
# El backfill histórico inicial tiene el mismo alcance que una corrida normal
# (todas las particiones); ver el runbook del Analytics Engineer.
set -euo pipefail

REPO="${REPO:-/home/ubuntu/oil-production-forecasting-platform}"
VENV="${VENV:-$HOME/dagster-venv}"
ENV_FILE="${ENV_FILE:-$REPO/infra/.env}"
MOD="data_pipeline.orchestration.definitions"

cd "$REPO"

# Credenciales del DW desde infra/.env (gitignoreado, chmod 600). El .env debe
# tener valores simples KEY=VALUE (sin espacios/caracteres de shell sin comillas).
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# shellcheck disable=SC1091
source "$VENV/bin/activate"

# DAGSTER_HOME persiste el historial de runs/logs (si no, Dagster usa un dir temporal
# por corrida). Default a ~/dagster-runtime; se puede override por entorno.
export DAGSTER_HOME="${DAGSTER_HOME:-$HOME/dagster-runtime}"
mkdir -p "$DAGSTER_HOME"

echo "[run_pipeline] $(date -Is) DB=$POSTGRES_DB full-reload"

# 1) Landing (descarga el CSV completo una vez, con retry) + catálogo de pozos.
dagster asset materialize -m "$MOD" --select produccion_raw
dagster asset materialize -m "$MOD" --select bronze_pozos

# 2) TODAS las particiones Bronze de producción desde el landing ya bajado
#    (full reload). Del propio landing se derivan los meses con datos y ya
#    cerrados (el mes en curso no es partición válida, end_offset=0). Reescribir
#    cada mes con el dato actual atrapa correcciones de cualquier antigüedad —no
#    solo de los últimos meses— y Silver resuelve la versión vigente (ADR-021).
PARTICIONES="$(python - <<'PY'
import pandas as pd
from datetime import date
from data_pipeline.extraction.extract_produccion import _LANDING_FILE

df = pd.read_parquet(_LANDING_FILE, columns=["anio", "mes"]).dropna()
inicio_mes_actual = date.today().replace(day=1)
pares = sorted({(int(a), int(m)) for a, m in zip(df["anio"], df["mes"])})
for anio, mes in pares:
    if date(anio, mes, 1) < inicio_mes_actual:   # solo meses ya cerrados
        print(f"{anio:04d}-{mes:02d}-01")
PY
)"

for part in $PARTICIONES; do
  echo "[run_pipeline] bronze_produccion partición $part"
  dagster asset materialize -m "$MOD" --select bronze_produccion --partition "$part"
done

# 3) Publicación: Bronze→Postgres + dbt (Silver/Gold/DQ). Un check `error` de
#    Data Quality hace fallar el job (frena la promoción a Gold).
dagster job execute -m "$MOD" -j dw_publish

echo "[run_pipeline] $(date -Is) OK"
