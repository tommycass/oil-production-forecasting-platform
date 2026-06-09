# Título: ADR-021: Estrategia de refresh de Bronze (full reload vs ventana incremental)

**Estado:** Aceptado

## Contexto

La corrida automática del pipeline (`data_pipeline/orchestration/run_pipeline.sh`,
disparada por cron) refresca la capa Bronze de producción antes de cargar a Postgres
y correr dbt (Silver/Gold/DQ). Hay que decidir **qué particiones `anio/mes` reescribe
cada corrida** desde el landing recién descargado.

La implementación inicial usaba una **ventana móvil**: reescribía solo las particiones
de los últimos `REFRESH_MONTHS` meses (3 por defecto), asumiendo que las correcciones
de la fuente caen sobre meses recientes.

Hechos que condicionan la decisión:

- **La fuente corrige meses ya publicados** (columna `rectificado`) y **no garantiza
  que la corrección sea de un mes reciente**: puede republicar un período viejo.
- **La fuente solo publica el archivo completo** (~144 MB con todo el histórico); no
  expone API incremental ni filtro "desde la última corrida" (ver ADR-012). El landing
  ya se descarga entero en cada corrida de todos modos.
- **`load_bronze.py` ya carga todas las particiones presentes en disco** a
  `bronze.produccion` con `if_exists="replace"` (full reload del lado del DW), y
  **Silver ya deduplica** por `(idpozo, anio, mes)` quedándose con la versión vigente
  (`silver_produccion_vigente.sql`: orden `rectificado desc, fecha_data desc`).
- **Bronze es inmutable y fiel al crudo** (ADR-013): reescribir una partición la deja
  igual al archivo actual de la fuente, sin acumular duplicados entre corridas.

### Problema detectado con la ventana móvil

Con la ventana de N meses, si la fuente corrige un mes **fuera** de esa ventana, su
partición en disco **nunca se reescribe**: el landing trae la corrección, pero el
parquet viejo queda congelado, `load_bronze` lo carga tal cual y Silver nunca ve la
corrección. **Las correcciones de meses viejos se pierden en silencio.**

### Alternativas evaluadas

| Criterio | Ventana móvil (N meses) | Detección de cambios (CDC) | Full reload (elegida) |
|---|---|---|---|
| Atrapa correcciones de cualquier antigüedad | ❌ solo dentro de la ventana | ✅ | ✅ |
| Requiere lógica de diff contra el estado previo | no | ✅ (compleja, frágil) | no |
| Viable con la fuente (sin API incremental) | sí | ⚠️ habría que reconstruir el "antes" | sí |
| Costo por corrida | bajo (N meses) | medio | alto (todas las particiones) |
| Coherencia con Silver/Gold (full-refresh `table`) | parcial | parcial | ✅ total |

## Decisión

**En cada corrida se reescriben TODAS las particiones de Bronze de producción** desde
el landing recién descargado (full reload), no solo las de los últimos meses. Las
particiones se derivan del propio landing (meses con datos y ya cerrados; el mes en
curso no es partición válida). Se elimina la ventana móvil (`REFRESH_MONTHS`).

La resolución de la versión vigente de cada mes corregido **sigue en Silver** (dedup
por `fecha_data`/`rectificado`), no en Bronze: Bronze conserva el crudo tal como llega.

### Por qué full reload sobre las alternativas

- **Sobre la ventana móvil:** elimina el gap silencioso de correcciones viejas. Como
  la fuente puede corregir cualquier período, la única ventana correcta es "todos".
- **Sobre la detección de cambios (CDC):** la fuente no expone qué cambió ni un filtro
  incremental; implementar un diff exigiría reconstruir y mantener el estado anterior,
  más lógica y más superficie de bugs, justo lo que el full reload evita.
- **El costo extra es asumible:** el landing se baja entero igual (sin descarga
  adicional) y reescribir las particiones de un dataset de ~144 MB son segundos; el
  cuello es el cómputo de reescritura, acotado a esta escala.
- **Reconcilia con ADR-012:** aquel ADR había **descartado** reescribir todas las
  particiones en cada corrida ("rewrite global") con el argumento de que
  "re-dispararía el reproceso aguas abajo aunque solo se haya corregido un mes". Esa
  objeción **ya no aplica**: Silver y Gold son full-refresh `table` (ADR-018, runbook del
  Analytics Engineer) y se reconstruyen completos en cada corrida, toque las particiones
  que toque. Sin ese costo marginal, reescribir todo es la opción robusta. Se conserva el
  **mecanismo de escritura por partición** que eligió ADR-012 (escritura aislada e
  idempotente); lo que cambia es el **alcance** del refresh automático: todas las
  particiones, no solo la corregida.

### Por qué no guardar snapshots datados de Bronze

No se conserva una copia por fecha de ingesta de producción (se sobrescribe, ver
retención asimétrica en ADR-013). **La historia de correcciones ya la aporta la fuente**
dentro de cada archivo (varias filas por `(idpozo, anio, mes)` distinguidas por
`fecha_data`); con full reload + dedup de Silver se aprovecha directamente esa historia,
sin duplicar 144 MB por corrida.

### Efecto sobre el particionado por mes (corrimiento de justificación)

El full reload **corre el rol** del particionado `anio/mes` que fijaron ADR-012 y ADR-013.
Su justificación original —reprocesar un mes puntual sin tocar el resto, de forma
**automática**— deja de ejercerse en el camino del cron, que ahora reescribe todos los
meses por igual. El particionado **se mantiene**, pero su valor pasa a apoyarse en:

- **Backfill manual dirigido (break-glass):** re-materializar una sola partición en
  segundos para forzar un mes corregido fuera de ciclo, sin correr el full reload entero
  (ver runbook del Data Engineer). Es la justificación de peso que queda.
- **Unidad de escritura del propio full reload:** el refresh está implementado como
  "escribir cada partición, aislada e idempotente"; la partición es el grano de escritura,
  no algo que el full reload esquive.
- **Organización e inspección** en disco (`anio=YYYY/mes=MM/`) y **observabilidad** por mes
  en el grafo de Dagster.

No aporta *partition pruning* a Silver, porque Silver lee la tabla `bronze.produccion` de
Postgres (cargada entera por `load_bronze.py`), no los parquet directamente; ese beneficio
solo aplicaría a un consumidor que leyera Bronze en disco. Mantener el particionado es
barato y habilita el break-glass, así que se conserva pese a que su argumento es más débil
que bajo la ventana móvil.

## Consecuencias

**Positivas:**
- Robustez: atrapa correcciones de cualquier antigüedad; cero gap silencioso.
- Simplicidad: sin lógica de detección de cambios ni de ventana a calibrar.
- Idempotencia y reproducibilidad: re-correr produce el mismo Bronze (reescribe, no
  acumula).
- Coherencia con el resto del pipeline: Silver y Gold ya son full-refresh `table`
  (ver runbook del Analytics Engineer); Bronze full reload mantiene una sola filosofía.

**Negativas:**
- Se reescriben todas las particiones en cada corrida aunque pocas hayan cambiado; el
  costo crece con el volumen histórico (acotado hoy, no indefinidamente).
- El refresh automático materializa partición por partición (un arranque de Dagster por
  mes): más lento que la ventana móvil, irrelevante para un cron mensual pero a tener en
  cuenta si la cadencia se acortara.

## Decisiones Técnicas Posteriores

- **Optimización de runtime (si hiciera falta):** materializar el rango completo en una
  sola corrida con `--partition-range` + `BackfillPolicy.single_run()` en el asset
  `bronze_produccion`, en lugar del loop por partición.
- **Escala:** si el volumen creciera al punto de que el full reload duela, evaluar un
  esquema incremental sobre la fact (mismo análisis que hace el Analytics Engineer para
  Silver/Gold).
- **Cadencia:** la frecuencia del cron se mantiene acorde al ritmo mensual de la fuente
  (ver runbook del Data Engineer); este ADR decide el *alcance* del refresh, no su
  frecuencia.
