"""Proyecto dbt expuesto a Dagster (dagster-dbt).

Define el `DbtProject` que apunta a `transform/` (perfil `oil_dw`, `profiles.yml`
env-driven por `POSTGRES_*`). dagster-dbt necesita el `manifest.json` para construir
un asset por modelo; se genera con `dbt parse` (lo corre el setup en AWS, o
`prepare_if_dev()` en desarrollo). Ver ADR-011 (orquestación) y ADR-016 (calidad).
"""

from dagster_dbt import DbtProject

from data_pipeline.config import PROJECT_ROOT

# El proyecto dbt vive en transform/; profiles.yml está en ese mismo directorio.
dw_dbt_project = DbtProject(project_dir=(PROJECT_ROOT / "transform").resolve())

# En desarrollo (DAGSTER_IS_DEV_CLI) regenera el manifest al vuelo; en AWS lo deja
# `dbt parse` durante el setup, antes de que el cron invoque a Dagster.
dw_dbt_project.prepare_if_dev()
