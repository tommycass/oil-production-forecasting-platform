-- Dimensión de calidad: FRESHNESS.
-- Falla (warn) si la última ingesta de producción supera el umbral de frescura.
-- Umbral placeholder de 7 días; se coordina con la cadencia real del DAG de
-- Dagster (ADR-016). Es `warn` (no bloquea): la frescura es una señal, no un
-- impedimento para promover datos ya validados.
{{ config(severity='warn', tags=['dq:freshness']) }}

select max(fecha_ingesta) as ultima_ingesta
from {{ ref('silver_produccion') }}
having max(fecha_ingesta) < current_timestamp - interval '7 days'
    or max(fecha_ingesta) is null
