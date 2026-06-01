-- Gold · fact_produccion_mensual. Grano: (pozo, mes) = (idpozo, anio, mes).
-- Las surrogate keys se recalculan con la misma función hash que las dims, así
-- el join es determinístico; las claves nulas apuntan al miembro '-1' (ADR-015).

with produccion as (
    select * from {{ ref('silver_produccion') }}
)

select
    case when idpozo is null
         then '-1' else {{ dbt_utils.generate_surrogate_key(['idpozo']) }} end          as sk_pozo,
    case when idempresa is null
         then '-1' else {{ dbt_utils.generate_surrogate_key(['idempresa']) }} end       as sk_operadora,
    case when idareayacimiento is null
         then '-1' else {{ dbt_utils.generate_surrogate_key(['idareayacimiento']) }} end as sk_yacimiento,
    case when anio is null or mes is null
         then '-1' else {{ dbt_utils.generate_surrogate_key(['anio', 'mes']) }} end      as sk_fecha,

    -- dimensión degenerada (trazabilidad directa contra Silver/Bronze)
    idpozo,

    -- medidas
    prod_pet,
    prod_gas,
    prod_agua,
    iny_agua,
    iny_gas,
    iny_co2,
    iny_otro,
    tef
from produccion
