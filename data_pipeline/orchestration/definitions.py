"""Punto de entrada de Dagster: assets del pipeline + recurso dbt + jobs.

Grafo end-to-end (ADR-011): Bronze(parquet) → Bronze(Postgres) → Silver/Gold/DQ.
El refresh del DW (`dw_publish`) se dispara headless por cron del SO (ver
`data_pipeline/orchestration/run_pipeline.sh` y el runbook del Analytics Engineer).
El retrain (Fase 3, ADR-041) define Schedule mensual + Sensor por datos nuevos →
requieren el dagster-daemon (ver runbook ml-retrain). Todo es env-driven (`POSTGRES_*`,
`MLFLOW_TRACKING_URI`), así el mismo código corre en staging y prod.

Levantar la UI localmente:
    dagster dev -m data_pipeline.orchestration.definitions
"""

from dagster import AssetSelection, Definitions, define_asset_job, load_assets_from_modules
from dagster_dbt import DbtCliResource

from data_pipeline.orchestration import assets, feature_store, retrain
from data_pipeline.orchestration.dbt_project import dw_dbt_project

# El feature store (Fase 3) se materializa con un asset Python (reusa ml/), no dbt;
# queda downstream de la carga de Bronze, así entra en dw_publish y se refresca con el DW.
# El retrain (Fase 3, ADR-041) suma su job + Schedule mensual + Sensor por datos nuevos.
all_assets = load_assets_from_modules([assets, feature_store, retrain])

# Job de "publicación": carga Bronze→Postgres y corre dbt (Silver/Gold/DQ) + materializa
# el feature store. Es lo que dispara el cron tras refrescar Bronze (ver run_pipeline.sh).
dw_publish_job = define_asset_job(
    name="dw_publish",
    selection=AssetSelection.assets(["bronze", "produccion"], ["bronze", "pozos"]).downstream(),
)

defs = Definitions(
    assets=all_assets,
    jobs=[dw_publish_job, retrain.retrain_job],
    schedules=[retrain.retrain_mensual],
    sensors=[retrain.retrain_por_features_nuevas],
    resources={
        "dbt": DbtCliResource(
            project_dir=dw_dbt_project,
            profiles_dir=str(dw_dbt_project.project_dir),
        ),
    },
)
