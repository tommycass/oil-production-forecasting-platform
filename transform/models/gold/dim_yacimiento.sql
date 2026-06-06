-- Gold · dim_yacimiento (SCD Type 1). Una fila por área/yacimiento, con su
-- cuenca y provincia (jerarquía geográfica denormalizada en la dimensión).

with src as (
    select idareayacimiento, yacimiento, cuenca, provincia
    from {{ ref('silver_produccion') }}
    where idareayacimiento is not null
),

agg as (
    select
        idareayacimiento,
        max(yacimiento) as yacimiento,
        max(cuenca)     as cuenca,
        max(provincia)  as provincia
    from src
    group by idareayacimiento
)

select
    {{ dbt_utils.generate_surrogate_key(['idareayacimiento']) }} as sk_yacimiento,
    idareayacimiento,
    yacimiento,
    cuenca,
    provincia
from agg

union all

select '-1', null, 'DESCONOCIDO', null, null
