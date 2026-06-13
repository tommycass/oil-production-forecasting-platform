-- KPI de frescura y cobertura del pipeline de datos.
-- Una sola fila que resume el estado operativo del DW para ser consumida
-- directamente por la tarjeta "Última Corrida Pipeline" del dashboard de Metabase.
-- estado_fresqueza = 'OK' si la última ingesta fue hace ≤ 35 días (ciclo mensual + buffer).
with fresqueza as (
    select max(fecha_ingesta)::date as ultima_ingesta
    from {{ ref('silver_produccion') }}
),

resumen as (
    select
        max(f.periodo)              as ultimo_periodo_disponible,
        count(distinct fp.idpozo)   as total_pozos_activos
    from {{ ref('fact_produccion_mensual') }} fp
    join {{ ref('dim_fecha') }} f on fp.sk_fecha = f.sk_fecha
    where f.periodo != 'DESCONOCIDO'
)

select
    fr.ultima_ingesta,
    current_date                                              as hoy,
    current_date - fr.ultima_ingesta                         as dias_sin_actualizar,
    case
        when current_date - fr.ultima_ingesta <= 35 then 'OK'
        else 'ATRASADO'
    end                                                       as estado_fresqueza,
    r.ultimo_periodo_disponible,
    r.total_pozos_activos

from fresqueza fr
cross join resumen r
