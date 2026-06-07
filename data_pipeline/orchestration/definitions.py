"""Punto de entrada de Dagster: assets del pipeline + recurso dbt + jobs.

Grafo end-to-end (ADR-011): Bronze(parquet) → Bronze(Postgres) → Silver/Gold/DQ.
Se dispara headless por cron en AWS (ver `data_pipeline/orchestration/run_pipeline.sh`
y el runbook del Analytics Engineer). No define schedules: la cadencia la pone el
cron del SO. Todo es env-driven (`POSTGRES_*`), así el mismo código corre en
staging (oil_dw_staging) y prod (oil_dw_prod).

Levantar la UI localmente:
    dagster dev -m data_pipeline.orchestration.definitions
"""

from dagster import AssetSelection, Definitions, define_asset_job, load_assets_from_modules
from dagster_dbt import DbtCliResource

from data_pipeline.orchestration import assets
from data_pipeline.orchestration.dbt_project import dw_dbt_project

all_assets = load_assets_from_modules([assets])

# Job de "publicación": carga Bronze→Postgres y corre dbt (Silver/Gold/DQ). Es lo
# que dispara el cron tras refrescar las particiones de Bronze (ver run_pipeline.sh).
dw_publish_job = define_asset_job(
    name="dw_publish",
    selection=AssetSelection.assets(["bronze", "produccion"], ["bronze", "pozos"]).downstream(),
)

defs = Definitions(
    assets=all_assets,
    jobs=[dw_publish_job],
    resources={
        "dbt": DbtCliResource(
            project_dir=dw_dbt_project,
            profiles_dir=str(dw_dbt_project.project_dir),
        ),
    },
)
