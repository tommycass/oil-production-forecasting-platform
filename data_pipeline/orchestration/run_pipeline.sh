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

# 0) Preflight: garantizar el manifest de dbt que consumen los assets Silver/Gold/DQ.
#    dagster-dbt registra un asset por modelo leyendo transform/target/manifest.json AL
#    IMPORTAR las definiciones; si ese archivo falta, el bloque @dbt_assets no se registra
#    (ver assets.py) y `dw_publish` cargaría SOLO Bronze, salteando Silver/Gold/DQ EN
#    SILENCIO. Lo regeneramos en cada corrida (idempotente y barato) para no depender de un
#    `dbt parse` de setup previo y para capturar cambios de modelos tras un git pull. El
#    DbtProject de dagster lee siempre <project_dir>/target/manifest.json (NO respeta
#    DBT_TARGET_PATH), por eso forzamos --target-path a esa ruta.
DBT_DIR="$REPO/transform"
DBT_MANIFEST="$DBT_DIR/target/manifest.json"
echo "[run_pipeline] preflight: asegurando el manifest de dbt → $DBT_MANIFEST"
[ -d "$DBT_DIR/dbt_packages" ] || ( cd "$DBT_DIR" && dbt deps )
( cd "$DBT_DIR" && dbt parse --profiles-dir . --target-path "$DBT_DIR/target" )
if [ ! -f "$DBT_MANIFEST" ]; then
  echo "[run_pipeline] ERROR: no se generó $DBT_MANIFEST; Silver/Gold/DQ no se ejecutarían. Abortando corrida." >&2
  exit 1
fi

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

# 4) (Opcional) Refrescar el catálogo de gobierno en DataHub con el linaje de esta
#    corrida (ADR-017). Best-effort: una caída de DataHub NO debe frenar el refresh
#    del DW, así que los fallos de este paso solo avisan, no abortan. Solo corre si
#    DATAHUB_GMS_HOST está seteado (infra/.env) y el CLI con extra [dbt] está en el
#    venv (pip install 'acryl-datahub[dbt,datahub-rest]'); si no, se omite.
if [ -n "${DATAHUB_GMS_HOST:-}" ] && command -v datahub >/dev/null 2>&1; then
  echo "[run_pipeline] DataHub: ingesta de linaje → $DATAHUB_GMS_HOST"
  TARGET="${DBT_TARGET_PATH:-$REPO/transform/target}"
  # dw_publish ya dejó manifest.json + run_results.json; catalog.json (linaje de
  # columna) requiere docs generate, que introspecciona el DW recién construido.
  ( cd "$REPO/transform" && dbt docs generate --profiles-dir . ) \
    || echo "[run_pipeline] WARN: dbt docs generate falló; ingesto sin catalog"
  datahub ingest -c "$REPO/infra/datahub/dbt_recipe.yml" \
    --set "source.config.manifest_path=$TARGET/manifest.json" \
    --set "source.config.catalog_path=$TARGET/catalog.json" \
    --set "source.config.run_results_paths[0]=$TARGET/run_results.json" \
    || echo "[run_pipeline] WARN: ingesta a DataHub falló (no bloquea el pipeline)"
else
  echo "[run_pipeline] DataHub: omitido (DATAHUB_GMS_HOST no seteado o CLI ausente)"
fi

echo "[run_pipeline] $(date -Is) OK"
