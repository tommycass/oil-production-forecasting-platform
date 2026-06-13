-- Ranking histórico de pozos por producción acumulada de petróleo y gas.
-- Incluye atributos descriptivos de pozo, operadora y yacimiento para facilitar
-- exploración en BI sin necesidad de joins manuales contra el modelo estrella.
-- Excluye el miembro desconocido (idpozo = -1).
select
    p.idpozo,
    p.sigla,
    p.tipo_de_recurso,
    p.clasificacion,
    p.formacion,
    o.operadora,
    y.yacimiento,
    y.cuenca,
    y.provincia,
    sum(fp.prod_pet)                as prod_pet_total_m3,
    sum(fp.prod_gas)                as prod_gas_total_mm3,
    sum(fp.prod_agua)               as prod_agua_total_m3,
    count(distinct fp.sk_fecha)     as meses_con_produccion,
    min(f.periodo)                  as primer_periodo,
    max(f.periodo)                  as ultimo_periodo,
    rank() over (order by sum(fp.prod_pet) desc)  as ranking_pet,
    rank() over (order by sum(fp.prod_gas) desc)  as ranking_gas

from {{ ref('fact_produccion_mensual') }} fp
join {{ ref('dim_pozo') }}       p on fp.sk_pozo       = p.sk_pozo
join {{ ref('dim_operadora') }}  o on fp.sk_operadora  = o.sk_operadora
join {{ ref('dim_yacimiento') }} y on fp.sk_yacimiento = y.sk_yacimiento
join {{ ref('dim_fecha') }}      f on fp.sk_fecha      = f.sk_fecha

where p.idpozo != -1

group by
    p.idpozo, p.sigla, p.tipo_de_recurso, p.clasificacion, p.formacion,
    o.operadora, y.yacimiento, y.cuenca, y.provincia
