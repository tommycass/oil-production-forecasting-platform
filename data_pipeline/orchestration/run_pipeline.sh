#!/usr/bin/env bash
# Refresh recurrente del DW, headless (para cron). Materializa el Bronze de los
# últimos meses (atrapa altas y meses corregidos vía `rectificado`), lo carga a
# Postgres y corre dbt (Silver/Gold/Data Quality). Env-driven: el mismo script
# sirve a staging y prod según el infra/.env de cada EC2.
#
# Uso:
#   data_pipeline/orchestration/run_pipeline.sh            # refresca REFRESH_MONTHS meses
#   REFRESH_MONTHS=6 .../run_pipeline.sh                   # ventana más amplia
#
# Para el backfill histórico inicial (todas las particiones), ver el runbook del
# Analytics Engineer: itera bronze_produccion sobre el rango real de años una vez.
set -euo pipefail

REPO="${REPO:-/home/ubuntu/oil-production-forecasting-platform}"
VENV="${VENV:-$HOME/dagster-venv}"
ENV_FILE="${ENV_FILE:-$REPO/infra/.env}"
REFRESH_MONTHS="${REFRESH_MONTHS:-3}"
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

echo "[run_pipeline] $(date -Is) DB=$POSTGRES_DB refresh=${REFRESH_MONTHS}m"

# 1) Landing (descarga el CSV completo una vez) + catálogo de pozos.
dagster asset materialize -m "$MOD" --select produccion_raw
dagster asset materialize -m "$MOD" --select bronze_pozos

# 2) Particiones Bronze de los últimos N meses YA COMPLETOS. La fuente es mensual y
#    se publica con atraso: el mes en curso aún no es una partición válida (la
#    MonthlyPartitionsDefinition con end_offset=0 solo incluye meses cerrados).
#    Refrescar meses recientes atrapa altas tardías y meses corregidos (rectificado).
for i in $(seq 1 "$REFRESH_MONTHS"); do
  mes="$(date -d "$i months ago" +%Y-%m-01)"
  echo "[run_pipeline] bronze_produccion partición $mes"
  dagster asset materialize -m "$MOD" --select bronze_produccion --partition "$mes"
done

# 3) Publicación: Bronze→Postgres + dbt (Silver/Gold/DQ). Un check `error` de
#    Data Quality hace fallar el job (frena la promoción a Gold).
dagster job execute -m "$MOD" -j dw_publish

echo "[run_pipeline] $(date -Is) OK"
