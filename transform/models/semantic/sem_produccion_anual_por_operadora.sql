-- Producción anual agregada por operadora.
-- Permite comparar el desempeño de cada empresa a nivel año sin exponer
-- las surrogate keys ni requerir joins al modelo estrella.
-- Filtra miembros desconocidos para mantener la integridad de los totales.
select
    o.operadora,
    f.anio,
    sum(fp.prod_pet)            as prod_pet_anual_m3,
    sum(fp.prod_gas)            as prod_gas_anual_mm3,
    sum(fp.prod_agua)           as prod_agua_anual_m3,
    count(distinct fp.sk_pozo)  as pozos_activos

from {{ ref('fact_produccion_mensual') }} fp
join {{ ref('dim_operadora') }} o on fp.sk_operadora = o.sk_operadora
join {{ ref('dim_fecha') }}     f on fp.sk_fecha     = f.sk_fecha

where o.operadora != 'DESCONOCIDA'
  and f.anio is not null

group by o.operadora, f.anio
