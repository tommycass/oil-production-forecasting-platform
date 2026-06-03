# Runbook — Data Engineer: reprocesar un mes de producción corregido por la fuente

Procedimiento para re-ingestar y propagar un mes de producción cuando
datos.gob.ar **republica datos ya publicados** (correcciones marcadas con la
columna `rectificado`). Aplica a la fuente *Producción de pozos de gas y petróleo
no convencional* del pipeline de Fase 2.

## Propósito y disparador

**Propósito:** dejar la capa Bronze —y, en cascada, Silver y Gold— reflejando la
versión corregida de un período (`anio/mes`), sin alterar el resto de los datos y
de forma idempotente.

**Cuándo se ejecuta (disparadores):**
- **Incidente / pedido:** un analista o la Persona B reporta que las cifras de un
  mes "cambiaron" o no cuadran contra la fuente oficial.
- **Alerta de calidad:** un check de freshness o de validez (ADR-016) marca que un
  período tiene registros con `rectificado = t` recién aparecidos.
- **Programado:** la corrida regular de extracción ya trae las correcciones; este
  runbook es para forzar/verificar el reproceso de un mes puntual fuera de ciclo.

## Rol, dueño y prerrequisitos

**Dueño:** Data Engineer (responsable de Bronze y de la orquestación).

**Prerrequisitos:**
- Acceso al repositorio y al entorno donde corre Dagster (local o el servicio del
  `docker-compose`).
- Entorno del pipeline instalado: `pip install -r data_pipeline/requirements.txt`.
- Conectividad a `datos.gob.ar` (la extracción descarga el CSV completo).
- Acceso de lectura al Data Warehouse (Postgres) para validar Silver/Gold, o
  coordinación con la Persona B (Analytics Engineer) para esa parte.
- Saber el período afectado: `anio` y `mes` a reprocesar.
