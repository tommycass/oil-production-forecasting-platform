-- Silver: producción mensual limpia, tipada y deduplicada. Grano: (idpozo, anio, mes).
-- Contiene SOLO filas válidas; las que fallan una regla de validez dura (medida
-- negativa, físicamente imposible) se desvían a la cuarentena `silver_produccion_rechazos`
-- (patrón quarantine: no se dropean en silencio, se persisten con su motivo para
-- auditoría y reconciliación bronze = silver + rechazos). Bronze retiene el crudo.
-- La lógica de tipado/dedup vive en `silver_produccion_vigente` (compartida, DRY).
-- Gate de Data Quality previo a Gold (ADR-016): los checks `error` bloquean Gold.

select
    idpozo, anio, mes,
    idempresa, operadora,
    sigla, formacion, profundidad,
    idareayacimiento, yacimiento, cuenca, provincia,
    coordenada_x, coordenada_y,
    tipo_de_recurso, clasificacion,
    prod_pet, prod_gas, prod_agua,
    iny_agua, iny_gas, iny_co2, iny_otro,
    tef, fecha_data, rectificado, fecha_ingesta
from {{ ref('silver_produccion_vigente') }}
where motivo_rechazo is null
