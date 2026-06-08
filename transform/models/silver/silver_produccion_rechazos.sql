-- Cuarentena (quarantine / dead-letter, ADR-016): filas de producción excluidas de
-- `silver_produccion` por fallar una regla de validez dura (medida negativa). En vez
-- de dropearlas en silencio (que rompería la reconciliación de conteos), se persisten
-- con su `motivo_rechazo` para auditoría y para reconciliar bronze = silver + rechazos.
-- Vive en el schema `dq` junto al resto de los artefactos de calidad. Full-refresh,
-- como el resto del DW (ADR: materialización table determinística).
{{ config(schema='dq', materialized='table') }}

select
    idpozo, anio, mes, operadora,
    prod_pet, prod_gas, prod_agua,
    iny_agua, iny_gas, iny_co2, iny_otro, tef,
    fecha_data, rectificado, fecha_ingesta,
    motivo_rechazo,
    current_timestamp as _detectado_en
from {{ ref('silver_produccion_vigente') }}
where motivo_rechazo is not null
