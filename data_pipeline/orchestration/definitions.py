"""Punto de entrada de Dagster: carga los assets del pipeline.

Levantar la UI localmente:
    dagster dev -m data_pipeline.orchestration.definitions
"""

from dagster import Definitions, load_assets_from_modules

from data_pipeline.orchestration import assets

defs = Definitions(assets=load_assets_from_modules([assets]))
