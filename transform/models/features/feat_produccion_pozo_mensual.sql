-- Feature store · feat_produccion_pozo_mensual (Fase 3, Rol 2).
--
-- Grano: (idpozo, anio, mes) sobre el UNIVERSO PETROLERO = pozos con al menos un
-- mes de prod_pet > 0 (ADR-028). Cada fila es el "mes base t": sus features están
-- disponibles AL momento t (anti-leakage) y el target `y_next` es prod_pet de t+1.
--
-- Reproduce EXACTO las definiciones de `ml/dataset.py` (mismo contrato) para que el
-- entrenamiento (Rol 1) y la inferencia (Rol 3) lean las MISMAS features y se evite
-- el training-serving skew (RNF Fase 3). Deriva de la capa Gold (fact + dims), así
-- el linaje Bronze→Silver→Gold→Features queda trazado en DataHub.
-- Contrato publicado: docs/feature-store.md · Decisión: ADR-031.

with fact as (
    select
        f.idpozo,
        d_fecha.anio,
        d_fecha.mes,
        make_date(d_fecha.anio, d_fecha.mes, 1) as periodo,
        d_fecha.trimestre,
        f.prod_pet,
        f.prod_gas,
        f.tef,
        d_pozo.profundidad,
        d_pozo.formacion,
        d_pozo.tipopozo,
        d_pozo.clasificacion,
        d_yac.cuenca,
        d_yac.provincia,
        d_op.operadora
    from {{ ref('fact_produccion_mensual') }} f
    join {{ ref('dim_fecha') }}      d_fecha on f.sk_fecha = d_fecha.sk_fecha
    join {{ ref('dim_pozo') }}       d_pozo  on f.sk_pozo = d_pozo.sk_pozo
    join {{ ref('dim_yacimiento') }} d_yac   on f.sk_yacimiento = d_yac.sk_yacimiento
    join {{ ref('dim_operadora') }}  d_op    on f.sk_operadora = d_op.sk_operadora
    where d_fecha.anio is not null and d_fecha.mes is not null  -- excluye miembro -1
),

-- Universo petrolero: pozos con al menos un mes de prod_pet > 0 (ADR-028).
universo_petrolero as (
    select idpozo
    from fact
    group by idpozo
    having max(prod_pet) > 0
),

petroleras as (
    select f.*
    from fact f
    join universo_petrolero u on f.idpozo = u.idpozo
),

-- Ventanas autoregresivas. shift/rolling/cumcount de pandas son POSICIONALES por
-- fila ordenada (anio, mes); `lag/lead/avg/row_number over (order by anio, mes)`
-- las reproduce 1 a 1 porque el grano hace (idpozo, anio, mes) única.
features as (
    select
        *,
        row_number()      over w                                       as _rn,
        lag(prod_pet, 1)  over w                                       as lag1,
        lag(prod_pet, 2)  over w                                       as lag2,
        lag(prod_pet, 3)  over w                                       as lag3,
        avg(prod_pet)     over (w rows between 2 preceding and current row) as _roll3,
        lag(tef, 1)       over w                                       as tef_lag1,
        lead(prod_pet, 1) over w                                       as y_next
    from petroleras
    window w as (partition by idpozo order by anio, mes)
)

select
    -- claves / entidad
    idpozo,
    anio,
    mes,
    periodo,
    trimestre,
    -- atributos estáticos (categóricas CRUDAS; el encoding lo hace el modelo, ADR-031)
    profundidad,
    formacion,
    tipopozo,
    clasificacion,
    cuenca,
    provincia,
    operadora,
    -- medidas crudas del mes t
    prod_pet,
    prod_gas,
    tef,
    -- features autoregresivas (anti-leakage, disponibles al mes t)
    lag1,
    lag2,
    lag3,
    -- media móvil 3m: NULL hasta tener 3 meses (= pandas rolling(3).mean(), min_periods=3)
    case when _rn >= 3 then _roll3 end as roll3,
    (_rn - 1) as antiguedad,            -- 0-based (= pandas cumcount())
    tef_lag1,
    -- target: prod_pet del mes siguiente (t+1). NULL en el último mes de cada pozo
    -- (aún sin observar) → esa fila sirve para inferencia, no para entrenamiento.
    y_next,
    -- metadata de reproducibilidad (RNF Fase 3)
    '{{ var("feature_set_version", "v1") }}' as feature_set_version,
    {{ dbt.current_timestamp() }} as computed_at
from features
