-- Gold · dim_fecha. Grano mensual (la fuente no tiene día). Se genera a partir
-- de las combinaciones (anio, mes) presentes en producción.

with periodos as (
    select distinct anio, mes
    from {{ ref('silver_produccion') }}
    where anio is not null and mes is not null
)

select
    {{ dbt_utils.generate_surrogate_key(['anio', 'mes']) }} as sk_fecha,
    anio,
    mes,
    ((mes - 1) / 3) + 1 as trimestre,
    lpad(anio::text, 4, '0') || '-' || lpad(mes::text, 2, '0') as periodo
from periodos

union all

select '-1', null, null, null, 'DESCONOCIDO'
