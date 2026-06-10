# Título: ADR-012: Tipo de carga de datos a la capa Bronze

**Estado:** Aceptado

## Contexto

La capa Bronze ingiere dos fuentes de datos.gob.ar con características muy
distintas, y la adenda técnica exige **definir y justificar explícitamente el
tipo de carga** (full refresh / incremental append / merge-upsert) por dataset.

Hechos relevantes que condicionan la decisión:

- **Listado de pozos:** catálogo de ~84k filas, sin grano temporal. Describe el
  estado actual de cada pozo.
- **Producción:** ~406k filas con grano `idpozo + anio + mes` (2006–2026). La
  fuente **corrige meses ya publicados**: trae la columna `rectificado` y puede
  incluir más de un registro para el mismo `(idpozo, anio, mes)` distinguidos por
  `fecha_data`.
- **La fuente no expone API incremental ni filtro temporal:** ambas se publican
  como un **único archivo CSV completo** que se descarga entero. No hay forma de
  pedir "solo lo nuevo desde la última corrida".
- **Bronze debe ser inmutable y fiel al crudo** (ver ADR-013 y `docs/data-model.md`):
  conserva el dato tal como llegó, incluida la historia de correcciones, que es
  insumo para auditoría y backfill.

Se evalúan tres estrategias de carga:

1. **Full refresh:** descargar todo y reemplazar lo persistido en cada corrida.
2. **Incremental append:** agregar solo los registros nuevos respecto de la
   corrida anterior.
3. **Merge / upsert:** insertar o actualizar por clave, resolviendo a un único
   registro vigente por entidad.

## Decisión

**La extracción desde la fuente es full en ambos datasets** (es la única opción:
la fuente solo publica el archivo completo). La diferencia está en cómo se
persiste en Bronze y dónde se resuelve la corrección de datos.

### Listado de pozos → full refresh (snapshot por fecha de ingesta)

Catálogo chico y sin grano temporal: se baja entero y se persiste como snapshot
versionado por fecha de ingesta. El costo de reescribirlo completo es trivial
(~84k filas) y mantener snapshots datados da trazabilidad del catálogo en el
tiempo a bajo costo.

### Producción → full refresh, materializado por partición (vía landing)

El archivo completo se baja a una zona de landing **una sola vez** y desde ahí se
escribe cada partición `anio/mes` por separado. La carga sigue siendo full (la
fuente obliga a bajar todo), pero la persistencia a Bronze es **por partición**.
Se evaluaron dos formas de materializar ese full refresh:

- **Rewrite global (descartada):** borrar Bronze entero y reescribir las ~244
  particiones en cada corrida. Es simple, pero reescribe meses que no cambiaron y
  "toca" todas las particiones, lo que re-dispararía el reproceso aguas abajo
  aunque solo se haya corregido un mes.
- **Por partición desde el landing (elegida):** materializar solo la partición del
  mes a reprocesar, leyendo del landing ya descargado. Reescribe únicamente ese
  mes, sin re-descargar ni re-disparar el resto, y habilita el backfill por
  partición de Dagster (ver ADR-011 y ADR-013). La escritura por partición es
  idempotente.

> **Actualización (ADR-021):** el *alcance del refresh automático* se revisó. El cron
> reescribe **todas** las particiones en cada corrida (full reload), no solo la
> corregida. El argumento que acá descartó el "rewrite global" —"re-disparar el reproceso
> aguas abajo"— dejó de aplicar al volverse Silver/Gold full-refresh (ADR-018): se
> reconstruyen enteros igual. Se **conserva** el mecanismo de escritura por partición
> (elegido acá); cambia solo cuántas particiones toca el refresh automático. Ver ADR-021.

### El merge/upsert se difiere a Silver, no se hace en Bronze

La resolución de los meses corregidos —quedarse con un único registro vigente por
`(idpozo, anio, mes)` según `fecha_data`— se realiza **en Silver** (Persona B),
no en Bronze. Bronze conserva **todos** los registros crudos, incluidas las
versiones corregidas, porque su rol es ser fiel e inmutable: esa historia es el
insumo para auditar y reprocesar.

### Por qué no las otras estrategias

- **Incremental append (descartada):** la fuente no expone un filtro "desde la
  última corrida", así que no podríamos traer solo lo nuevo de forma confiable; y
  como la fuente reemite meses corregidos, el append duplicaría registros en
  lugar de actualizarlos.
- **Merge/upsert en Bronze (descartada para esta capa):** es la estrategia
  correcta para resolver correcciones, pero aplicarla en Bronze destruiría la
  historia cruda (sobrescribiría el registro original con el corregido). Por eso
  el upsert vive en Silver, sobre un Bronze que permanece inmutable.

## Consecuencias

**Positivas:**
- Bronze inmutable y fiel: conserva la historia de correcciones, habilitando
  auditoría y reprocesamiento.
- El particionado de producción por `anio/mes` habilita el backfill de un mes
  puntual, requisito de la consigna.
- Cargas idempotentes en ambos datasets: re-correr no duplica.
- Estrategia simple y robusta frente a una fuente que solo ofrece descarga
  completa, sin depender de mecanismos de captura incremental inexistentes.

**Negativas:**
- Se descarga el archivo completo en cada corrida aunque cambien pocos meses;
  para el volumen actual (~144 MB) el costo es asumible, pero no escala
  indefinidamente.
- La resolución de duplicados por corrección queda fuera de Bronze: un consumidor
  que lea Bronze directo verá registros duplicados por `(idpozo, anio, mes)` y
  debe ir a Silver para el dato vigente.
- Mantener snapshots datados del catálogo de pozos crece en almacenamiento con el
  tiempo (acotado por el tamaño chico del dataset).
