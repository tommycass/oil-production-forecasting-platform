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

## Pasos

1. **Confirmar el período y la corrección.** Identificar el `anio/mes` reportado y
   verificar contra la fuente que efectivamente hay registros corregidos
   (aparecen filas con `rectificado = t` o cambió la `fecha_data` del período).

2. **Re-ingestar a Bronze.** Re-ejecutar la extracción de producción. Como la
   fuente publica el archivo completo, esto re-descarga todo y **reescribe todas
   las particiones** `anio/mes` (incluida la corregida) de forma idempotente.
   - Vía Dagster (recomendado): materializar el asset `bronze_produccion` desde la
     UI (`dagster dev -m data_pipeline.orchestration.definitions`) o por CLI:
     ```bash
     dagster asset materialize --select bronze_produccion \
       -m data_pipeline.orchestration.definitions
     ```
   - Vía función directa (fallback sin orquestador):
     ```bash
     python -m data_pipeline.extraction.extract_produccion
     ```

3. **Verificar Bronze.** Confirmar que la partición del mes quedó reescrita y que
   contiene la versión corregida:
   ```bash
   python -c "import pandas as pd; df=pd.read_parquet('data/bronze/produccion/anio=2024/mes=3/produccion.parquet'); print(len(df), df['rectificado'].value_counts().to_dict())"
   ```
   (Reemplazar `anio=2024/mes=3` por el período afectado.)

4. **Propagar a Silver y Gold.** Disparar el reproceso aguas abajo de ese período,
   coordinando con la Persona B (Analytics Engineer). Silver deduplica por
   `(idpozo, anio, mes)` conservando el registro vigente (último `fecha_data`), de
   modo que el dato corregido reemplaza al anterior; Gold se re-materializa a
   partir de Silver. El reproceso es por partición (mes), no global.

## Validación

Sé que el reproceso salió bien cuando:

- **Bronze:** la partición del mes existe y se reescribió en esta corrida (timestamp
  del parquet actualizado), y `rectificado` muestra los registros corregidos
  esperados (paso 3).
- **Conteos coherentes:** la cantidad de filas y los totales de producción
  (`prod_gas`, `prod_pet`) del mes en Gold cambian respecto del valor previo en la
  dirección reportada, y no varían los otros meses.
- **Calidad:** la tabla de resultados de DQ (esquema `dq`) registra los checks del
  período como *pasados*, sin fallas críticas (schema, completeness, validity).
- **Frescura:** la marca de última actualización del período refleja la corrida de
  hoy (visible también en el grafo de assets de Dagster y, si está integrado, en
  DataHub).
- **Idempotencia:** re-correr el paso 2 deja el mismo resultado (no duplica filas
  ni particiones).

## Si algo falla

- **Falla la descarga (fuente caída o lenta):** la extracción es idempotente; se
  puede re-ejecutar sin riesgo. Con Dagster, los reintentos con backoff
  (incremento que se suma en el paso 5 del roadmap) cubren los errores
  transitorios; si persiste, esperar y reintentar más tarde.
- **Bronze quedó con datos sospechosos tras reescribir:** Bronze de producción es
  *full refresh* sin snapshots datados (ver retención en ADR-013), así que **no
  hay versión anterior en el repo para restaurar**. Plan B: la fuente es la verdad
  de referencia → volver a ejecutar la extracción; si la propia fuente publicó un
  dato erróneo, **no promover** y escalar.
- **El gate de calidad bloquea Silver→Gold (ADR-016):** Bronze queda actualizado
  pero Gold no se promueve. Escalar a la **Persona B (Analytics Engineer)** para
  investigar la falla de DQ del período; no forzar la promoción.
- **Escalamiento:** problemas de Silver/Gold/DQ → Analytics Engineer; sospecha de
  que la fuente oficial publicó datos incorrectos → elevar al equipo antes de
  propagar la corrección.
