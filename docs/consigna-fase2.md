# Consigna Fase 2 — Integración de Datos (Referencia rápida)

> **Fecha de entrega: 15 de junio de 2026.**
> Nota: el PRD original menciona 09/06 para Fase 2, pero la adenda técnica
> (documento más específico y posterior) fija el **15/06**. Se toma esta última.

Objetivo de la fase: implementar la funcionalidad de **ingesta, manejo y
procesamiento de datos** sobre la cual se construirá el modelo predictivo de
Fase 3.

---

## Entregables obligatorios

1. **Repositorio Git** con el código del pipeline de datos
2. **README** actualizado con instrucciones para:
   - Actualizar/correr los workflows (DAGs)
   - Acceder al sistema de BI
   - Acceder a la plataforma de gobierno de datos
   - Descripción de la arquitectura de datos desarrollada
3. **ADRs** (en `docs/adr/`) cubriendo las decisiones clave de Fase 2 —
   ver lista abajo. *ADRs sin comparación de alternativas reales = inválidos.*
4. **Documentación del modelo de datos** (`docs/data-model.md`): grano de la
   fact table, dimensiones, surrogate keys, decisión de SCD.
5. **Runbooks** (en `docs/runbooks/`): al menos 2 roles distintos, uno de perfil
   negocio y uno de perfil implementación. Un archivo por rol.
6. **Servicio accesible** por URL durante el período de corrección
7. **Video demo de ~5 minutos**

---

## Lo que debe estar funcionando

### 🅰️ Data Engineer — Ingesta + Orquestación (Integrante 1 — Micol)
Zona del diagrama: **Data Sources → Extracción → Bronze + Orquestación**
(franja inferior). *Arranca primero, baja dependencia de los demás.*

- **Scripts de extracción** de las 2 fuentes de datos.gob.ar:
  - Producción de pozos de gas/petróleo no convencional
  - Listado de pozos cargados por operadoras (info complementaria)
- **Capa Bronze**: persistencia del crudo (parquet/csv versionados por fecha de
  ingesta, o tabla bronze en el DW)
- **Orquestador** con DAGs definidos como código (Prefect / Dagster / Airflow):
  - Idempotencia (re-run no duplica)
  - Retries con backoff exponencial
  - Observabilidad mínima: logs y status accesibles
- **Backfill / reprocesamiento histórico**: procedimiento documentado y
  ejecutable (ej. `prefect deployment run ... --param start=2023-01`)
- **ADRs**:
  - ADR-011: herramienta de orquestación (Airflow vs Prefect vs Dagster)
  - ADR-012: tipo de carga (full vs incremental append vs merge/upsert) — justificar por dataset
  - ADR-013: diseño de la capa Bronze (formato, particionado, retención)
- **Runbook** `docs/runbooks/data-engineer.md`: procedimiento "reprocesar un mes
  de producción que llegó tarde / corregido por la fuente"
- **README**: sección "Workflows — cómo actualizar/correr DAGs, backfill, troubleshooting"

### 🅱️ Analytics Engineer — Modelado + Data Quality (Integrante 2)
Zona del diagrama: **Bronze → Silver → Gold → DW + Data Quality** (caja central).
*Dependencia media: espera Bronze.*

- **Capa Silver**: limpieza (tipos, deduplicación, normalización de nombres de
  pozos/operadoras, manejo de nulos)
- **Capa Gold / modelo estrella**:
  - Fact: producción mensual por pozo (grano explícito)
  - Dims: `dim_pozo`, `dim_operadora`, `dim_yacimiento`, `dim_fecha` con surrogate keys
  - Decisión de SCD (Type 1 vs Type 2) justificada para dims que cambian
- **Data Warehouse**: modelo estrella obligatorio (DuckDB file-based o Postgres multi-user)
- **Data Quality**: mínimo **3 dimensiones de calidad** (schema, completeness,
  validity, uniqueness, freshness):
  - Resultados **persistidos en tabla** (no solo asserts en runtime)
  - Fallar un check crítico DEBE tener consecuencia operativa: bloqueo de
    promoción Silver→Gold + alerta / marca de calidad visible
- **ADRs**:
  - ADR-014: arquitectura Medallion (vs Lakehouse vs ETL clásico)
  - ADR-015: modelo dimensional estrella (vs snowflake / Data Vault / OBT)
  - ADR-016: estrategia de Data Quality (Great Expectations vs dbt tests vs Soda)
- **Runbook** `docs/runbooks/analytics-engineer.md` (o `data-steward.md`)

### 🅲️ Data Analyst — BI + Gobierno + Demo (Integrante 3)
Zona del diagrama: **DW → BI Platform / Data Governance → Admins/Usuarios**.
*Dependencia alta: espera Gold + orquestador.*

- **Plataforma BI** (Metabase o Superset) conectada al DW, dashboards para no técnicos:
  - Producción mensual por yacimiento
  - Top pozos por producción
  - Frescura de datos (última ingesta)
- **Plataforma de gobierno** (DataHub vía docker-compose):
  - Ingesta de metadata desde el DW
  - Ingesta del orquestador → **lineage a nivel tabla** end-to-end (Bronze→Gold)
  - Workflows de extracción visibles, datos del DW, última actualización
- **Integración** del docker-compose existente con los nuevos servicios
- (bonus) Semantic layer (dbt metrics, Cube.dev o vistas lógicas)
- **ADRs**:
  - ADR-017: plataforma de gobierno (DataHub vs OpenMetadata vs Amundsen vs Marquez)
  - ADR-018: plataforma BI (Metabase vs Superset vs Redash)
- **Runbook** `docs/runbooks/bi-user.md` (o `data-analyst.md`) — perfil negocio (obligatorio)
- **README**: secciones "Acceso a BI", "Acceso a gobierno" y "Arquitectura de datos"
- Lleva el hilo del **video demo**

---

## Requerimientos funcionales clave (de la adenda)

- Proceso de **extracción** de las 2 fuentes datos.gob.ar
- Arquitectura **Medallion** (Bronze / Silver / Gold)
- Plataforma de **BI** para usuarios no técnicos
- Plataforma de **gobierno de datos** (workflows, datos del DW, última actualización)
- **Orquestador** con DAGs como código: idempotencia, retries con backoff, observabilidad
- Procedimiento de **backfill** documentado y verificable
- Tipo de carga (full/incremental/merge/upsert) definido y justificado en ADR
- (bonus) **Semantic layer**

---

## Requerimientos no funcionales clave

- **Data Warehouse en modelo estrella**
- Procesamiento **idempotente** y reprocesable por fecha
- **Chequeos de calidad** persistidos (≥3 dimensiones, incluyendo schema y linaje),
  con consecuencia operativa al fallar
- **Linaje** navegable a nivel tabla (gobierno con DataHub o equivalente justificado)
- Documentación del modelo de datos: grano, dimensiones, surrogate keys, SCD
- **Runbooks** por rol (mín. 2 roles: uno negocio + uno implementación), cada uno con:
  propósito/disparador, dueño/prerrequisitos, pasos, validación, rollback/escalamiento,
  consideraciones no funcionales, + 1 decisión funcional y 1 no funcional justificadas
  desde la perspectiva e incentivos del rol

---

## ADRs requeridos (resumen)

| ADR | Decisión | Dueño |
|-----|----------|-------|
| ADR-011 | Herramienta de orquestación | A |
| ADR-012 | Tipo de carga (full/incremental/merge) | A |
| ADR-013 | Diseño de capa Bronze | A |
| ADR-014 | Arquitectura Medallion | B |
| ADR-015 | Modelo dimensional estrella | B |
| ADR-016 | Estrategia de Data Quality | B |
| ADR-017 | Plataforma de gobierno | C |
| ADR-018 | Plataforma BI | C |

> Todo ADR DEBE comparar al menos 2 alternativas con trade-offs reales.

---

## Cronograma sugerido (→ 15/jun)

**Semana 1 (2–8/jun) — Cimientos en paralelo**
- A: extracción de las 2 fuentes local + decidir orquestador + ADR-011/012
- B: schema Silver/Gold en papel + ADR-014/015 + elegir DW
- C: ADR-017/018 + levantar Metabase y DataHub vacíos en compose
- *Sync miércoles: contratos de interfaz entre capas*

**Semana 2 (9–13/jun) — Integración**
- A: DAGs end-to-end con idempotencia + backfill probado
- B: Silver/Gold materializadas + DQ corriendo + tabla de resultados + ADR-016
- C: dashboards en Metabase contra Gold + lineage en DataHub
- *Viernes 13: prueba end-to-end + freeze de features*

**14–15/jun — Cierre:** README final, runbooks, video demo, PR de release a main.

---

## Convención de trabajo

- 1 PR por feature, sin commits directos a main/staging
- Cada PR pide review de al menos 1 de los otros 2
- Branches: `fase2/A-extraccion-fuentes`, `fase2/B-modelo-estrella`, `fase2/C-datahub-setup`
- ADRs y runbooks: PR aparte por documento
- Commits sin co-autoría de Claude

---

## Riesgos a vigilar

- **DataHub es pesado** (varios contenedores): C lo levanta en semana 1 aunque sea vacío
- **Lineage automático** depende del orquestador: A y C acuerdan en semana 1 qué
  orquestador emite metadata compatible con DataHub (Airflow tiene mejor integración nativa)
- **Datos sucios** en datos.gob.ar: A los baja y mira el día 1 para detectar sorpresas
- **ADRs inválidos**: sin comparación de alternativas no cuentan

---

## Criterios de evaluación

- Cumplimiento de requisitos en tiempo y forma
- Capacidad de responder preguntas sobre diseño y funcionamiento:
  supuestos y limitaciones, trade-offs de alternativas, justificación técnica
