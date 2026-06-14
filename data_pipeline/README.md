# data_pipeline

Pipeline de datos de la Fase 2 (Integración de Datos). Cubre la **ingesta** desde
las fuentes públicas de datos.gob.ar y la persistencia del crudo en la **capa
Bronze** de la arquitectura Medallion.

> Zona del Data Engineer: `Data Sources → Extracción → Bronze`.
> Las capas Silver/Gold y el modelo estrella viven aguas abajo (Analytics Engineer).

## Estructura

```
data_pipeline/
  config.py              URLs de las fuentes + ruta de Bronze (definidas una sola vez)
  extraction/
    extract_pozos.py     Listado de pozos      → Bronze  (full refresh)
    extract_produccion.py Producción por pozo  → Bronze  (full refresh por partición; merge vigente en Silver)
  orchestration/
    assets.py            Assets Dagster: Bronze(parquet) → Bronze(Postgres) → dbt
    definitions.py       Punto de entrada + recurso dbt + job dw_publish
    dbt_project.py       Proyecto dbt (transform/) expuesto a dagster-dbt
    run_pipeline.sh      Refresh headless para cron (env-driven: staging/prod)
```

> **Nota (jun-2026):** el grafo de Dagster se **extiende al DW** (carga a Postgres +
> modelos dbt Silver/Gold/DQ vía `dagster-dbt`) para correr end-to-end en AWS. Ese
> tramo lo agregó el Analytics Engineer y fue **revisado y aceptado por el Data Engineer**
> (ver "Actualización (jun-2026)" en ADR-011, con la decisión formal en ADR-018 y el
> runbook del Analytics Engineer).

Los datos crudos se escriben en `data/bronze/` (fuera de este paquete y
gitignoreado: ver `data/.gitignore`). Solo se versiona la estructura, nunca los
archivos de datos.

## Fuentes

| Fuente | Clave | Grano | Estrategia de carga |
|--------|-------|-------|---------------------|
| Producción de pozos (no convencional) | `idpozo` | idpozo + anio + mes | full refresh por partición; el merge vigente por `(idpozo, anio, mes)` se resuelve en Silver (la fuente corrige meses). Ver ADR-012 |
| Listado de pozos por operadora | `idpozo` | un pozo por fila | full refresh (catálogo chico) |

## Estado

Extracción, orquestador (ADR-011) y backfill por mes **implementados** (assets de
Dagster en `orchestration/`, con retries y particiones mensuales). El grafo se extiende
al DW (carga a Postgres + dbt) para el flujo end-to-end en AWS; ese tramo fue revisado y
aceptado por el Data Engineer (ADR-011 / ADR-018). Procedimiento operativo en el runbook
del Analytics Engineer.
