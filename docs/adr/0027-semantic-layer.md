# Título: ADR-027: Capa semántica sobre el modelo estrella de Gold

**Estado:** Aceptado

> Complementa [ADR-014](0014-arquitectura-medallion.md) (Medallion), [ADR-015](0015-modelo-dimensional-estrella.md) (modelo estrella) y [ADR-020](0020-plataforma-bi.md) (Metabase como plataforma de BI). Este ADR decide **cómo exponer métricas de negocio pre-calculadas** a usuarios de BI y a la API, sin obligarlos a conocer el modelo estrella ni a escribir joins contra surrogate keys.

---

## Contexto

El esquema `gold.*` está diseñado para ser correcto y eficiente como modelo dimensional: una fact table con surrogate keys enteras y cuatro dimensiones conformadas. Esa estructura es óptima para una herramienta de BI con soporte de join drag-and-drop, pero tiene dos fricciones para casos de uso comunes:

1. **Usuarios de negocio en Metabase**: cuando construyen una pregunta ad-hoc, deben unir `fact_produccion_mensual` con las cuatro dimensiones usando las columnas `sk_*`. Para un analista que no conoce el modelo dimensional, eso es una barrera. Además, si quieren ver "producción por yacimiento por mes", tienen que recordar cuáles columnas sumar (`prod_pet`) y cuáles promediar (`tef`), lo que genera errores frecuentes.

2. **La API REST**: el endpoint `/api/v1/forecast` y potenciales endpoints de reporting necesitan acceder a datos agregados sin construir SQL complejo en la capa de aplicación. Hoy el servicio consume `gold.dim_pozo` directamente (busca IDs de pozos); si en el futuro necesita métricas más complejas, tener vistas semánticas accesibles desde Postgres simplifica la capa de servicio.

### Alternativas evaluadas

#### Alternativa A: dbt MetricFlow (capa semántica nativa de dbt)

MetricFlow es la solución oficial de dbt para definir métricas en YAML y calcularlas on-demand mediante una CLI o un servidor semántico. Está disponible desde dbt Core 1.6.

**Ventajas:**
- Definición declarativa de métricas en YAML (single source of truth).
- Integración nativa con `dbt build` y el linaje de DataHub.
- Soporta slicing por cualquier dimensión sin pre-materializar todas las combinaciones.

**Desventajas:**
- El servidor semántico que ejecuta las consultas (dbt Semantic Layer gateway) requiere dbt Cloud o una integración específica por herramienta de BI. Metabase **no tiene integración con dbt MetricFlow** a la fecha de esta decisión.
- MetricFlow no materializa las métricas como tablas/vistas SQL estándar accesibles desde Postgres. Las herramientas de BI deben consultar a través del gateway dbt, no directamente al DW.
- Introducir el gateway añadiría una nueva pieza de infraestructura sin beneficio para el stack actual (Metabase + PostgreSQL).

**Veredicto:** descartada. La dependencia del gateway lo hace incompatible con el stack actual.

#### Alternativa B: Cube.dev (motor semántico standalone)

Cube.dev es un servidor semántico independiente que se conecta al DW, expone una API de consulta y permite definir "cubes" (entidades de negocio con métricas y dimensiones).

**Ventajas:**
- Potente y flexible. Soporta caché, pre-agregaciones y múltiples clientes (Metabase puede conectarse vía SQL proxy).
- Separación total de la lógica de negocio de la lógica del DW.

**Desventajas:**
- Implica desplegar y mantener un nuevo servicio (contenedor Node.js con ~500 MB), con su propia capa de configuración, autenticación y operación.
- El uso de Cube con Metabase requiere configurar Cube SQL API, que actúa como un Postgres virtual. No es una integración nativa; es un workaround documentado pero no estable.
- Exceso de ingeniería para el volumen y la frecuencia de consultas actuales.

**Veredicto:** descartada. El overhead operativo no se justifica para el tamaño del proyecto.

#### Alternativa C: Vistas SQL en un esquema `semantic` de dbt (seleccionada)

Crear modelos dbt con `+materialized: view` en un esquema dedicado `semantic.*`. Cada vista pre-aplica los joins y agrupaciones más comunes, expone nombres de columna en lenguaje de negocio y filtra miembros desconocidos. Las vistas referencian el modelo estrella con `{{ ref() }}`, quedando trazadas en el linaje de DataHub automáticamente.

**Ventajas:**
- **Sin infraestructura adicional:** las vistas son objetos SQL nativos en PostgreSQL; Metabase las consume exactamente igual que tablas.
- **Linaje completo:** DataHub ingiere el `manifest.json` y registra que `semantic.sem_top_pozos` depende de `gold.fact_produccion_mensual`, `gold.dim_pozo`, etc.
- **Contrato estable para BI:** el usuario de Metabase apunta al esquema `semantic`, que abstrae el modelo estrella. Si se cambia la estructura interna de Gold, las vistas semánticas se actualizan sin que el dashboard se rompa.
- **Mantenimiento mínimo:** no hay servidor extra. Las vistas se recalculan on-read contra Gold (que ya está materializado como tabla), así que no hay que correr un job adicional.
- **Alineado con el stack:** el Analytics Engineer ya conoce dbt; añadir modelos en `models/semantic/` es el mismo workflow que en `models/gold/`.

**Desventajas:**
- Las vistas no pre-calculan nada: cada consulta en Metabase ejecuta la vista contra las tablas Gold (que tienen ~millones de filas). Para el volumen actual (datos de una fuente pública de pozos no convencionales en Argentina) el rendimiento es aceptable; si el dataset crece 100×, puede ser necesario materializar algunas vistas como tablas o añadir índices.
- No soporta consultas de métricas dinámicas (e.g., "ventas en el percentil 90 del último trimestre rodante"); eso requeriría MetricFlow u otra solución dinámica. El caso de uso actual es análisis histórico y no requiere esa flexibilidad.

---

## Decisión

Implementar la **Alternativa C**: vistas SQL en el esquema `semantic.*` de dbt, con `+materialized: view`.

Se crean cuatro vistas semánticas iniciales:

| Modelo | Descripción |
|---|---|
| `sem_produccion_mensual_por_yacimiento` | Producción mensual de petróleo, gas y agua, agrupada por yacimiento + cuenca + provincia + período. Caso de uso: dashboard "Producción por yacimiento". |
| `sem_top_pozos` | Ranking histórico de pozos con atributos descriptivos completos (operadora, yacimiento, clasificación). Caso de uso: tabla "Top pozos por producción". |
| `sem_kpi_pipeline` | Fila única con KPIs operativos del pipeline: fecha de última ingesta, días sin actualizar, estado de frescura ('OK'/'ATRASADO') y período más reciente en Gold. Caso de uso: tarjeta "Última Corrida Pipeline" en Metabase. |
| `sem_produccion_anual_por_operadora` | Producción anual por operadora. Caso de uso: comparativa interanual de empresas. |

### Criterios de diseño de las vistas

1. **Sin surrogate keys en la salida:** las columnas `sk_*` se usan internamente para los joins pero no se seleccionan. El usuario de BI ve nombres de negocio (`yacimiento`, `operadora`, `sigla`).
2. **Filtrar miembros desconocidos:** cada vista excluye el miembro `'DESCONOCIDO'`/`'DESCONOCIDA'`/`idpozo=-1` para que los totales sean significativos.
3. **Semántica de agregación documentada:** las columnas numéricas llevan sufijo `_m3` o `_mm3` para indicar unidad. `tef` no se incluye en las vistas actuales porque es semi-aditiva (se promediaría, no sumaría) y requiere contexto para interpretarse correctamente; se puede agregar en el futuro con una nota explícita.
4. **Referenciadas con `{{ ref() }}`:** el linaje queda registrado en el `manifest.json` y visible en DataHub.

### Configuración en dbt_project.yml

```yaml
models:
  oil_dw:
    semantic:
      +schema: semantic
      +materialized: view
```

---

## Consecuencias

**Positivas:**
- Los usuarios de Metabase pueden construir dashboards apuntando al esquema `semantic` sin conocer el modelo estrella ni aplicar joins manuales.
- El linaje Bronze → Silver → Gold → Semantic queda completo y trazable en DataHub con cero configuración extra.
- El Analytics Engineer puede agregar nuevas vistas semánticas siguiendo exactamente el mismo workflow de dbt que para Gold.
- El contrato con BI se vuelve más estable: cambios internos en Gold se absorben en las vistas semánticas sin tocar los dashboards.

**Negativas:**
- Si el dataset crece significativamente, algunas vistas (especialmente `sem_top_pozos` con `rank() over`) pueden volverse lentas. La mitigación sería cambiar `+materialized: view` a `+materialized: table` en la vista afectada, o añadir índices en las tablas Gold subyacentes.
- No reemplaza al modelo estrella para consultas avanzadas ad-hoc que necesiten dimensiones cruzadas de forma dinámica; para eso el usuario técnico seguirá usando `gold.*` directamente.

## Relación con otros ADRs

- **ADR-014:** define la arquitectura Medallion. La capa `semantic` es una extensión opcional sobre Gold, no una capa nueva del Medallion; no rompe la arquitectura pero la extiende con un contrato de consumo estable.
- **ADR-015:** define el modelo estrella en Gold. Las vistas semánticas abstraen ese modelo para los consumidores no técnicos.
- **ADR-017:** DataHub ingiere el `manifest.json` y registrará el linaje hasta `semantic.*` sin cambios en la receta de ingesta.
- **ADR-020:** Metabase es la herramienta de BI. La alternativa A (MetricFlow) fue descartada precisamente porque Metabase no la soporta sin gateway.
