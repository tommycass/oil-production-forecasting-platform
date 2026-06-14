-- Producción mensual agregada por yacimiento, cuenca y provincia.
-- Caso de uso primario: dashboard de BI "Producción por yacimiento" y consultas ad-hoc
-- de analistas de negocio que no deben conocer el modelo estrella subyacente.
-- Filtra miembros desconocidos para evitar polución de los totales.
select
    y.yacimiento,
    y.cuenca,
    y.provincia,
    f.periodo,
    f.anio,
    f.mes,
    f.trimestre,
    sum(fp.prod_pet)                as prod_pet_total_m3,
    sum(fp.prod_gas)                as prod_gas_total_mm3,
    sum(fp.prod_agua)               as prod_agua_total_m3,
    count(distinct fp.sk_pozo)      as pozos_activos

from {{ ref('fact_produccion_mensual') }} fp
join {{ ref('dim_yacimiento') }} y on fp.sk_yacimiento = y.sk_yacimiento
join {{ ref('dim_fecha') }}      f on fp.sk_fecha      = f.sk_fecha

where y.yacimiento != 'DESCONOCIDO'
  and f.periodo    != 'DESCONOCIDO'

group by
    y.yacimiento, y.cuenca, y.provincia,
    f.periodo, f.anio, f.mes, f.trimestre
