-- Gold · dim_operadora (SCD Type 1). Una fila por empresa operadora.
-- La relación time-variant pozo→operadora se captura en el grano de la fact,
-- no acá (ADR-015). Se toma el nombre normalizado por idempresa desde producción
-- (única fuente con la razón social).

with src as (
    select idempresa, max(operadora) as operadora
    from {{ ref('silver_produccion') }}
    where idempresa is not null
    group by idempresa
)

select
    {{ dbt_utils.generate_surrogate_key(['idempresa']) }} as sk_operadora,
    idempresa,
    operadora
from src

union all

select '-1' as sk_operadora, null as idempresa, 'DESCONOCIDA' as operadora
