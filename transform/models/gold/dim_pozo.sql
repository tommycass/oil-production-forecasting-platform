-- Gold · dim_pozo (SCD Type 1). Surrogate key sobre idpozo. Incluye miembro
-- "desconocido" (sk = '-1') para FKs no resueltas en la fact (ADR-015).
--
-- Se construye con TODOS los pozos vistos en el catálogo (silver_pozos) y en
-- producción (silver_produccion): el catálogo aporta los atributos; producción
-- garantiza que ningún pozo de la fact quede sin fila en la dimensión.

with catalogo as (
    select * from {{ ref('silver_pozos') }}
),

prod_pozos as (
    select
        idpozo,
        max(sigla)           as sigla,
        max(formacion)       as formacion,
        max(profundidad)     as profundidad,
        max(tipo_de_recurso) as tipo_de_recurso,
        max(clasificacion)   as clasificacion,
        max(tipopozo)        as tipopozo,
        max(coordenada_x)    as coordenada_x,
        max(coordenada_y)    as coordenada_y
    from {{ ref('silver_produccion') }}
    where idpozo is not null
    group by idpozo
),

combinado as (
    select
        coalesce(c.idpozo, p.idpozo)                   as idpozo,
        coalesce(c.sigla, p.sigla)                     as sigla,
        coalesce(c.formacion, p.formacion)             as formacion,
        coalesce(c.profundidad, p.profundidad)         as profundidad,
        coalesce(c.tipo_de_recurso, p.tipo_de_recurso) as tipo_de_recurso,
        coalesce(c.clasificacion, p.clasificacion)     as clasificacion,
        p.tipopozo                                     as tipopozo,
        coalesce(c.coordenada_x, p.coordenada_x)       as coordenada_x,
        coalesce(c.coordenada_y, p.coordenada_y)       as coordenada_y
    from catalogo c
    full outer join prod_pozos p on c.idpozo = p.idpozo
)

select
    {{ dbt_utils.generate_surrogate_key(['idpozo']) }} as sk_pozo,
    idpozo,
    sigla,
    formacion,
    profundidad,
    tipo_de_recurso,
    clasificacion,
    tipopozo,
    coordenada_x,
    coordenada_y
from combinado

union all

select
    '-1'         as sk_pozo,
    -1           as idpozo,
    'DESCONOCIDO' as sigla,
    null         as formacion,
    null         as profundidad,
    null         as tipo_de_recurso,
    null         as clasificacion,
    null         as tipopozo,
    null         as coordenada_x,
    null         as coordenada_y
