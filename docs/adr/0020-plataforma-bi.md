# Título: ADR-020: Plataforma de BI

**Estado:** Aceptado

## Contexto

La adenda exige una **plataforma de BI en la que usuarios no técnicos puedan
revisar los datos**. La consigna concreta tres tableros mínimos: producción
mensual por yacimiento, top pozos por producción y frescura de datos (última
ingesta). El handoff del Analytics Engineer deja el contrato listo del lado del dato:

- El consumo va contra el esquema **`gold.*`** (modelo estrella ya poblado:
  `fact_produccion_mensual` + dims con surrogate keys), más `dq.*` para un tablero
  de calidad opcional.
- El DW es **PostgreSQL 16 en RDS**, accesible solo dentro de la VPC (por security
  group), no desde fuera. La herramienta de BI corre dentro de la VPC.
- El público objetivo son **usuarios no técnicos**: la facilidad de uso pesa tanto
  como la capacidad de visualización.

La decisión es *con qué herramienta* construimos el BI sobre ese contrato.

### Evaluación de alternativas

| Criterio (derivado de la consigna y el stack) | Metabase | Apache Superset | Redash |
|---|---|---|---|
| Facilidad para usuarios **no técnicos** | **Alta** (query builder visual, "ask a question") | Media (potente pero curva alta) | Baja (orientado a SQL) |
| Conector PostgreSQL | Nativo | Nativo | Nativo |
| Velocidad de setup (deadline 15/06) | **Muy alta** (un contenedor, asistente inicial) | Media (más config, metadata DB, workers) | Media |
| Riqueza de visualización | Buena (suficiente para los 3 tableros) | **Muy alta** (la más completa) | Media |
| Capa semántica / métricas reutilizables | Básica (Models/Metrics) | Sí (datasets + métricas) | No |
| Peso operativo (contenedores/recursos) | **Bajo** (app + su DB) | Alto (web, worker, beat, Redis, metadata DB) | Medio |
| Provisioning como código (para versionar) | Sí (export/serialization, vía API/archivos) | Sí (import/export YAML) | Parcial |
| Embedding / share para el demo | Sí (public links, embedding) | Sí | Sí |

**Superset** es la herramienta más poderosa en visualización y la única con una
capa semántica seria, pero su peso operativo (web + worker Celery + beat + Redis +
DB de metadata) y su curva de aprendizaje no se justifican para tres tableros
dirigidos a no técnicos, y suma contenedores en una infra ya ajustada de RAM.
**Redash** es liviano y bueno para analistas, pero es **SQL-first**: el usuario no
técnico choca con que casi todo arranca escribiendo una query, justo lo contrario
del requisito; además su mantenimiento upstream viene flojo. **Metabase** está
pensado exactamente para el caso "usuario no técnico explora datos": arma preguntas
y tableros con un constructor visual sobre el esquema `gold`, se levanta con **un
solo contenedor**, conecta nativo a Postgres y llega rápido a tableros presentables
—clave para el video demo y para el deadline.

## Decisión

Usamos **Metabase** como plataforma de BI, desplegado como **un servicio más del
docker-compose**, conectado a la base **`oil_dw_prod`** (y `oil_dw_staging` para
preview/QA), modelando sobre el esquema **`gold.*`**.

### Tableros (mínimos de la consigna)

1. **Producción mensual por yacimiento** — `sum(prod_pet)`/`sum(prod_gas)` por
   `dim_yacimiento` y `dim_fecha.periodo`.
2. **Top pozos por producción** — ranking por `idpozo` (dimensión degenerada) sobre la fact.
3. **Frescura de datos** — última ingesta vía `max(fecha)` / `dq.dq_results` (señal de freshness).
4. *(opcional)* **Tablero de calidad** sobre `dq.dq_results` + `dq.silver_produccion_rechazos`.

### Cuidados de modelado (del contrato del Analytics Engineer)

- Sumar **solo medidas aditivas** (`prod_pet`, `prod_gas`, `prod_agua`, `iny_*`);
  **`tef` se promedia, no se suma**.
- Joins siempre por surrogate keys `sk_*`; ejes temporales por `dim_fecha.periodo` (`AAAA-MM`).

### Conectividad

Metabase corre dentro de la VPC; se habilita la **regla de security group** del RDS
para admitir el SG del host de Metabase (mismo prerrequisito que gobierno,
[ADR-017](0017-plataforma-gobierno-datos.md)). La contraseña del DW no va al repo:
se inyecta por variable de entorno desde `infra/.env`.

## Consecuencias

**Positivas:**
- Camino más corto a tableros presentables para no técnicos y para el demo.
- Un solo contenedor liviano → entra en la infra sin reestructurar.
- Conexión nativa a `gold.*`; no hace falta SQL de limpieza (la estrella ya está lista).

**Negativas:**
- Menos potencia de visualización y capa semántica más básica que Superset (suficiente para el alcance, pero limita análisis exploratorio avanzado).
- La definición de métricas vive parcialmente en Metabase; si se quiere "definir una sola vez" hay que apoyarse en Models/Metrics o en un semantic layer aparte (bonus).
- Hay que versionar el provisioning (export) para que los tableros no queden solo en el estado de la app.

## Decisiones Técnicas Posteriores

- **Persistencia de Metabase (resuelto):** la metadata interna de Metabase vive en una
  base Postgres dedicada (`metabase_app`), **no en H2**, para que los tableros y cuentas
  sobrevivan reinicios del contenedor. Configurado en `infra/docker-compose.yml` (perfil
  `bi`, variables `MB_DB_*`).
- **Provisioning como código:** definir cómo se versionan los tableros (export/serialization vía API).
- **Cuentas/roles** para usuarios no técnicos (lectura) vs admin.
- **Apuntar a prod vs staging** según entorno; regla de SG coordinada con A.
