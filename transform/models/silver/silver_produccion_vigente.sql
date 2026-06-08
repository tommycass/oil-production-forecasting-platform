-- Intermedio (efímero, ADR-014): producción tipada y deduplicada por la clave de
-- negocio (idpozo, anio, mes), conservando el registro vigente (rectificado y última
-- ingesta). Marca con `motivo_rechazo` las filas con medidas físicamente imposibles
-- (negativas), para que aguas abajo se separen en `silver_produccion` (válidas) y
-- `silver_produccion_rechazos` (cuarentena). No materializa tabla propia: es la lógica
-- compartida por ambos (DRY), inlineada como CTE en cada uno.
{{ config(materialized='ephemeral') }}

with source as (
    select * from {{ source('bronze', 'produccion') }}
),

typed as (
    select
        cast(nullif(trim(cast(idpozo as varchar)), '') as integer)      as idpozo,
        cast(nullif(trim(cast(anio as varchar)), '') as integer)        as anio,
        cast(nullif(trim(cast(mes as varchar)), '') as integer)         as mes,
        nullif(trim(cast(idempresa as varchar)), '')                    as idempresa,
        upper(trim(cast(empresa as varchar)))                           as operadora,
        nullif(trim(cast(sigla as varchar)), '')                        as sigla,
        nullif(trim(cast(formacion as varchar)), '')                    as formacion,
        cast(nullif(trim(cast(profundidad as varchar)), '') as numeric) as profundidad,
        nullif(trim(cast(idareayacimiento as varchar)), '')             as idareayacimiento,
        upper(trim(cast(areayacimiento as varchar)))                    as yacimiento,
        upper(trim(cast(cuenca as varchar)))                            as cuenca,
        initcap(trim(cast(provincia as varchar)))                       as provincia,
        cast(nullif(trim(cast(coordenadax as varchar)), '') as numeric) as coordenada_x,
        cast(nullif(trim(cast(coordenaday as varchar)), '') as numeric) as coordenada_y,
        upper(trim(cast(tipo_de_recurso as varchar)))                   as tipo_de_recurso,
        upper(trim(cast(clasificacion as varchar)))                     as clasificacion,
        coalesce(cast(nullif(trim(cast(prod_pet as varchar)), '') as numeric), 0)  as prod_pet,
        coalesce(cast(nullif(trim(cast(prod_gas as varchar)), '') as numeric), 0)  as prod_gas,
        coalesce(cast(nullif(trim(cast(prod_agua as varchar)), '') as numeric), 0) as prod_agua,
        coalesce(cast(nullif(trim(cast(iny_agua as varchar)), '') as numeric), 0)  as iny_agua,
        coalesce(cast(nullif(trim(cast(iny_gas as varchar)), '') as numeric), 0)   as iny_gas,
        coalesce(cast(nullif(trim(cast(iny_co2 as varchar)), '') as numeric), 0)   as iny_co2,
        coalesce(cast(nullif(trim(cast(iny_otro as varchar)), '') as numeric), 0)  as iny_otro,
        coalesce(cast(nullif(trim(cast(tef as varchar)), '') as numeric), 0)       as tef,
        cast(nullif(trim(cast(fecha_data as varchar)), '') as date)     as fecha_data,
        lower(trim(cast(rectificado as varchar))) in ('t', 'true', '1') as rectificado,
        cast(fecha_ingesta as timestamp)                                as fecha_ingesta
    from source
),

dedup as (
    select
        *,
        row_number() over (
            partition by idpozo, anio, mes
            order by rectificado desc, fecha_data desc nulls last, fecha_ingesta desc
        ) as _rn
    from typed
)

select
    idpozo, anio, mes,
    idempresa, operadora,
    sigla, formacion, profundidad,
    idareayacimiento, yacimiento, cuenca, provincia,
    coordenada_x, coordenada_y,
    tipo_de_recurso, clasificacion,
    prod_pet, prod_gas, prod_agua,
    iny_agua, iny_gas, iny_co2, iny_otro,
    tef, fecha_data, rectificado, fecha_ingesta,
    -- Regla de validez dura: volúmenes y tiempos no pueden ser negativos. El crudo
    -- público trae algunos negativos (físicamente imposibles, NO marcados como
    -- rectificado). Se marcan acá para enviarlos a cuarentena, no a Silver.
    case
        when prod_pet  < 0 or prod_gas < 0 or prod_agua < 0
          or iny_agua  < 0 or iny_gas  < 0 or iny_co2   < 0 or iny_otro < 0
          or tef       < 0
        then 'medida negativa (produccion/inyeccion/tef)'
    end as motivo_rechazo
from dedup
where _rn = 1
