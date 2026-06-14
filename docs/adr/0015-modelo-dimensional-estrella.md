# Título: ADR-015: Modelo dimensional del Data Warehouse (estrella)

**Estado:** Aceptado

## Contexto

La adenda exige que el Data Warehouse use **modelo estrella** y que documentemos grano de la fact, dimensiones, surrogate keys y decisión de SCD. Este ADR justifica la elección del estilo de modelado dimensional y la estrategia de SCD; el detalle campo por campo vive en `docs/data-model.md`.

El consumo de la capa Gold es analítico y de BI: **BI (Metabase)** arma dashboards (producción mensual por yacimiento, top pozos, frescura) y **gobierno (DataHub)** navega el linaje a nivel tabla. Los datos llegan desde Silver con grano fila-por-registro: producción a grano `idpozo + anio + mes` y un catálogo de pozos a grano `idpozo`. El volumen es moderado (producción no convencional, **~406 mil filas históricas** verificadas contra el dataset real completo, ver ADR-019; catálogo ~84k pozos).

Las fuentes vienen **denormalizadas**: la tabla de producción ya trae `empresa`, `sigla`, `areayacimiento`, `cuenca`, `provincia` y `tipo_de_recurso` en cada fila. Eso nos da libertad para elegir el estilo de modelado en Gold sin estar atados a la forma del origen.

### Evaluación de alternativas

| Criterio | Estrella | Snowflake | Data Vault | OBT (one big table) |
|---|---|---|---|---|
| Exigido por la consigna | **Sí** | No | No | No |
| Simplicidad de queries para BI (Metabase) | Alta (1 join por dim) | Media (joins encadenados) | Baja (hubs/links/satélites) | Máxima (sin joins) |
| Legibilidad para usuarios no técnicos | Alta | Media | Baja | Media (tabla muy ancha) |
| Redundancia / tamaño | Baja-media | Baja | Baja | Alta (dims repetidas por fila) |
| Manejo de historia (SCD) | Nativo (Type 1/2) | Nativo | Excelente (satélites) | Pobre |
| Esfuerzo de implementación | Bajo | Medio | Alto | Muy bajo |
| Encaje con surrogate keys + conformed dims | Directo | Directo | Indirecto | No aplica |

**Snowflake** normalizaría las dimensiones (p. ej. `dim_yacimiento → dim_cuenca → dim_provincia`), ahorrando algo de espacio a costa de joins encadenados que complican los dashboards sin beneficio real a nuestra escala. **Data Vault** (hubs, links, satélites) brilla para integrar muchas fuentes cambiantes con auditoría histórica fuerte, pero es desproporcionado para dos fuentes y un equipo de tres en dos semanas; además su consumo requiere una capa de presentación encima (que terminaría siendo… una estrella). **OBT** (una sola tabla ancha con todo desnormalizado) elimina joins y es tentadora para una demo, pero repite los atributos de pozo/operadora/yacimiento en cada fila mensual, dificulta el slicing por dimensión, complica la calidad (no hay dónde garantizar unicidad de pozos) y no expresa surrogate keys ni SCD, que la consigna pide documentar.

## Decisión

Modelamos Gold como **esquema estrella** con una fact y cuatro dimensiones conformadas:

- **`fact_produccion_mensual`** — grano: **una fila por `(pozo, mes)`**, es decir `idpozo + anio + mes`. Medidas aditivas: `prod_pet`, `prod_gas`, `prod_agua`, `iny_agua`, `iny_gas`, `iny_co2`, `iny_otro`; semi-aditiva/no aditiva: `tef` (tiempo efectivo). Claves foráneas: `sk_pozo`, `sk_operadora`, `sk_yacimiento`, `sk_fecha`.
- **`dim_pozo`** — un registro por pozo (`idpozo`). Atributos descriptivos: sigla, formación productiva, profundidad, tipo de recurso, clasificación, coordenadas.
- **`dim_operadora`** — un registro por empresa operadora (`idempresa`): razón social normalizada.
- **`dim_yacimiento`** — un registro por área/yacimiento: yacimiento, cuenca, provincia.
- **`dim_fecha`** — un registro por mes calendario: `anio`, `mes`, trimestre, etiqueta.

Todas las dimensiones usan **surrogate keys de texto** (`sk_*`), generadas en Gold como **hash MD5** de la clave de negocio (`dbt_utils.generate_surrogate_key`), no las claves naturales de la fuente. Esto desacopla la fact de cambios en los identificadores de origen, habilita SCD y simplifica los joins a una sola columna.

### Decisión de SCD: Type 1 en `dim_pozo` y `dim_operadora`

La consigna pide decidir y justificar SCD para las dimensiones que cambian. La relación que genuinamente cambia en el tiempo es **pozo → operadora** (los pozos se transfieren entre empresas). La clave de nuestra decisión es **dónde** capturamos ese cambio:

- La tabla de producción trae la `empresa` operadora **en cada fila mensual**. Por lo tanto, la asociación pozo↔operadora *en el momento de la producción* queda registrada en el **grano de la fact**, no en la dimensión. La fact apunta a `sk_operadora` derivada de la empresa de ese mes.
- En consecuencia, **no metemos la operadora dentro de `dim_pozo`** (evitamos la trampa clásica de Kimball de jamonear un atributo time-variant en una dimensión, que forzaría SCD Type 2). Cada dimensión describe una entidad estable.
- Con la historia de la relación ya preservada en la fact, las dimensiones solo necesitan reflejar el **estado descriptivo actual** de cada entidad. Optamos por **SCD Type 1 (overwrite)** en `dim_pozo` y `dim_operadora`: si una operadora se renombra o un pozo se reclasifica, sobrescribimos. Bronze conserva la historia cruda para auditoría/backfill, así que no perdemos trazabilidad del dato.

**Alternativa considerada — Type 2 en `dim_pozo`:** versionar atributos como `clasificacion`/`tipo_estado` permitiría análisis "as-of" del estado del pozo. La descartamos porque las preguntas analíticas de este proyecto (producción por pozo, área, cuenca y tipo de recurso a lo largo del tiempo) se responden con el grano mensual de la fact y la operadora ya histórica; el costo de mantener `valid_from/valid_to`, flags de versión vigente y claves vigentes no se justifica para el alcance de la fase. Queda como evolución futura si Fase 3 requiere correctitud point-in-time de atributos de pozo.

## Consecuencias

**Positivas:**
- Dashboards de Metabase con un solo join por dimensión: simples y rápidos para usuarios no técnicos.
- Surrogate keys aíslan la fact de cambios de IDs en la fuente y dan unicidad controlada por dimensión (insumo directo de los checks de calidad, ADR-016).
- La asociación histórica pozo↔operadora queda en la fact, evitando la complejidad de SCD2 sin perder esa información.

**Negativas:**
- Type 1 pierde el historial de cambios de atributos *descriptivos* en las dimensiones (p. ej. reclasificación de un pozo); aceptable porque Bronze lo retiene y el análisis no lo requiere hoy.
- Cierta redundancia controlada en las dimensiones denormalizadas (propia de la estrella) frente a snowflake.

## Decisiones Técnicas Posteriores

- **Generación de surrogate keys:** `dbt_utils.generate_surrogate_key` sobre la clave natural, materializado en Gold.
- **Miembro "desconocido":** cada dimensión incluye una fila técnica (sk = -1) para FKs nulas/no resueltas en la fact, evitando perder filas en los joins.
- **`dim_fecha` mensual:** se genera por rango de fechas presente en producción; grano mensual porque ese es el grano de la fuente (no hay día).
