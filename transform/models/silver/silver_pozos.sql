-- Silver: catálogo de pozos limpio y tipado (un registro por idpozo).
-- Alimenta dim_pozo, dim_operadora y dim_yacimiento en Gold.

with source as (
    select * from {{ source('bronze', 'pozos') }}
),

typed as (
    select
        cast(nullif(trim(cast(idpozo as varchar)), '') as integer)      as idpozo,
        nullif(trim(cast(sigla as varchar)), '')                        as sigla,
        nullif(trim(cast(formprod as varchar)), '')                     as formacion,
        nullif(trim(cast(idempresa as varchar)), '')                    as idempresa,
        nullif(trim(cast(idareayacimiento as varchar)), '')             as idareayacimiento,
        upper(trim(cast(areayacimiento as varchar)))                    as yacimiento,
        upper(trim(cast(cuenca as varchar)))                            as cuenca,
        initcap(trim(cast(provincia as varchar)))                       as provincia,
        cast(nullif(trim(cast(profundidad as varchar)), '') as numeric) as profundidad,
        cast(nullif(trim(cast(coordenadax as varchar)), '') as numeric) as coordenada_x,
        cast(nullif(trim(cast(coordenaday as varchar)), '') as numeric) as coordenada_y,
        upper(trim(cast(clasificacion as varchar)))                     as clasificacion,
        upper(trim(cast(tipo_reservorio as varchar)))                   as tipo_de_recurso,
        cast(fecha_ingesta as timestamp)                                as fecha_ingesta
    from source
),

dedup as (
    select
        *,
        row_number() over (
            partition by idpozo
            order by fecha_ingesta desc
        ) as _rn
    from typed
)

select
    idpozo, sigla, formacion, idempresa,
    idareayacimiento, yacimiento, cuenca, provincia,
    profundidad, coordenada_x, coordenada_y,
    clasificacion, tipo_de_recurso, fecha_ingesta
from dedup
where _rn = 1
