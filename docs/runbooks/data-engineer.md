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
- **Programado:** la corrida regular hace **full reload** de todo Bronze (ADR-021),
  así que ya absorbe las correcciones de cualquier mes sin intervención; este runbook
  es para **forzar/verificar** el reproceso de un mes puntual fuera de ciclo.

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

2. **Re-ingestar el mes a Bronze.** Para forzar un mes puntual sin esperar al cron,
   refrescar el landing y re-materializar **solo esa partición** (rápido, no toca el
   resto):
   - Vía Dagster (recomendado), reemplazando `2024-03` por el mes afectado:
     ```bash
     MOD=data_pipeline.orchestration.definitions
     dagster asset materialize -m $MOD --select produccion_raw          # refresca el landing
     dagster asset materialize -m $MOD --select bronze_produccion --partition 2024-03-01
     ```
   - **Full reload** (todo Bronze, idéntico a lo que hace el cron): correr
     `data_pipeline/orchestration/run_pipeline.sh`, o como fallback sin orquestador
     `python -m data_pipeline.extraction.extract_produccion`.

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
  puede re-ejecutar sin riesgo. Con Dagster, los reintentos con backoff exponencial
  cubren los errores transitorios; si persiste, esperar y reintentar más tarde.
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

## Consideraciones no funcionales

Límites y garantías que el Data Engineer ownea en este procedimiento:

- **Frescura:** Bronze debe reflejar la última publicación de la fuente dentro de
  la cadencia definida (ver decisión no funcional abajo); las correcciones urgentes
  se cubren fuera de ciclo con este runbook.
- **Costo:** cada corrida descarga el archivo completo (~144 MB) y reescribe todas
  las particiones. Acotado hoy, pero no escala indefinidamente.
- **Calidad de dato:** el DE garantiza **fidelidad** del crudo (sin transformar,
  con el BOM correctamente descartado), no la limpieza (eso lo valida Silver/DQ).
- **Idempotencia:** re-ejecutar no duplica ni corrompe; es seguro reintentar.
- **Seguridad / PII:** la fuente es pública (datos.gob.ar) y no contiene datos
  personales; el riesgo de privacidad es bajo.
- **Gobernanza:** la corrida queda trazada (grafo de assets de Dagster y, si está
  integrado, linaje a nivel tabla en DataHub).

## Decisiones del procedimiento

### Decisión funcional: Bronze conserva el crudo sin deduplicar

El procedimiento **no resuelve la corrección en Bronze**: guarda todos los
registros tal como llegan (incluida la versión vieja y la corregida) y delega a
Silver quedarse con el vigente por `fecha_data`. Esta decisión la ownea y empuja
el Data Engineer por un interés propio y concreto: cuando un analista discute un
número ("este mes cambió, ¿por qué?"), el primero a quien le preguntan es al DE, y
la única forma de responder con autoridad es **poder mostrar qué dijo la fuente
antes y después de la corrección**. Si el DE deduplicara en Bronze, destruiría esa
evidencia y quedaría sin defensa ante una disputa o una auditoría. Conservar el
crudo íntegro es, para el DE, su seguro: le permite probar que el pipeline es fiel
y reprocesar desde una base confiable en vez de depender de que la fuente todavía
tenga el dato.

### Decisión no funcional: la extracción programada corre con cadencia mensual

La corrida automática se programa **mensual** (cron `0 3 5 * *`), acompañando el ritmo de
la fuente, que publica producción con cadencia aproximadamente mensual (ver ADR-018; este
runbook cubre los reprocesos urgentes entre corridas). La decisión responde a los
incentivos del DE, expuesto por dos lados opuestos: si el dato queda viejo, los consumidores
(analistas y la Persona B) se quejan y el DE es el responsable; pero si el job corre de más,
son descargas de 144 MB y reescrituras de todo Bronze (full reload, ADR-021) desperdiciadas,
más oportunidades de fallas transitorias que el DE tiene que ir a vigilar. Como la fuente
cambia ~mensualmente y el full reload de cada corrida ya captura correcciones de cualquier
antigüedad, mensual cubre el caso normal sin overhead; las correcciones urgentes entre
corridas se fuerzan con el reproceso dirigido de este runbook. Diaria o semanal serían puro
babysitting: el dato casi no cambia en ese lapso.
