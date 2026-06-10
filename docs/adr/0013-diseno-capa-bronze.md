# Título: ADR-013: Diseño de la capa Bronze

**Estado:** Aceptado

## Contexto

Bronze es la primera capa de la arquitectura Medallion (ver ADR-014): persiste el
crudo extraído de datos.gob.ar antes de cualquier transformación, y es la fuente
de la que Silver consume (ver `docs/data-model.md`). Su diseño define cómo se
guarda ese crudo, y hay que decidir cuatro aspectos, cada uno con alternativas:

- **Formato de archivo:** ¿guardamos el CSV tal como se descarga, o lo
  convertimos a un formato columnar como **parquet**?
- **Manejo de tipos:** ¿inferimos tipos al leer (números, fechas), o guardamos
  **todo como texto** para no alterar el dato?
- **Particionado:** ¿cómo organizamos los archivos en disco para que Silver lea
  eficientemente y se pueda reprocesar por período?
- **Ubicación y retención:** dónde viven los archivos y cuánto se conservan.

Restricciones y hechos que condicionan el diseño:

- Bronze debe ser **inmutable y fiel al crudo** (su valor es ser la copia
  auditable de lo que llegó); la limpieza es responsabilidad de Silver.
- Las dos fuentes traen un **BOM** al inicio del archivo que, si no se descarta
  al decodificar, ensucia el nombre de la primera columna.
- Producción tiene grano temporal (`anio/mes`) y requiere **backfill por mes**;
  el listado de pozos no tiene grano temporal.
- Los datos crudos **no se versionan en git** (`data/.gitignore`); solo se
  versiona la estructura de carpetas.

## Decisión

### Formato: parquet

El crudo se persiste en **parquet**, no en CSV. Parquet es columnar y comprimido
(los ~144 MB de producción ocupan mucho menos), trae el esquema embebido, y lo
leen de forma nativa y eficiente las herramientas aguas abajo (pandas, DuckDB,
Postgres, el orquestador). Se descarta **conservar el CSV crudo**: sería el más
fiel, pero pesado, lento de leer y sin esquema; la fidelidad ya se garantiza con
el manejo de tipos (abajo), no con el formato del archivo.

### Manejo de tipos: todo como texto

Las columnas se guardan **todas como texto** (sin inferir números ni fechas). Así
Bronze es fiel al literal de la fuente: no se pierden ceros a la izquierda, no se
reinterpretan formatos de fecha ni separadores decimales. El casteo a tipos
correctos lo hace Silver. Se descarta **inferir tipos en Bronze**: es cómodo pero
arriesga alterar el dato (p. ej. un código numérico que pierde un cero inicial).
Sí se corrige el **encoding** (lectura con `utf-8-sig` para descartar el BOM):
eso es decodificar bien, no transformar el dato.

### Particionado: por la naturaleza de cada fuente

- **Producción → `anio=YYYY/mes=MM/`** (grano de negocio). Es lo que permite
  reprocesar un mes puntual sin tocar el resto (backfill) y que Silver lea solo
  los períodos que necesita.
- **Listado de pozos → `ingesta=AAAA-MM-DD/`** (fecha de ingesta). No tiene grano
  temporal de negocio, así que se versiona por cuándo se trajo, conservando
  snapshots del catálogo.

Se descarta usar un **único esquema para ambas**: no comparten naturaleza
(una tiene grano temporal y necesita backfill; la otra es un catálogo de estado).

> **Actualización (ADR-021):** la justificación del particionado de producción se refinó.
> "Reprocesar un mes puntual sin tocar el resto" pasó a ser la vía del **backfill manual**
> (el refresh automático hace full reload de todos los meses). Y "que Silver lea solo los
> períodos que necesita" **no se ejerce** hoy: Silver lee la tabla `bronze.produccion` de
> Postgres (cargada entera), no los parquet. El particionado se conserva por el backfill
> manual, por ser la unidad de escritura del full reload y por organización. Ver ADR-021.

### Landing: una sola descarga, derivar las particiones

Producción se baja primero completa a una zona de **landing** (`data/landing/`), y
las particiones de Bronze se derivan de ese archivo. Se evaluaron dos formas de
poblar las particiones:

- **Descargar por partición (descartada):** que cada partición mensual traiga lo
  suyo. Inviable: la fuente solo publica el archivo completo, así que materializar
  un mes implicaría bajar los 144 MB enteros, y un backfill de varios meses, una
  descarga por cada uno.
- **Descarga única → derivar particiones (elegida):** un paso `produccion_raw` baja
  el CSV una vez al landing; cada partición de Bronze lee de ahí y escribe solo su
  mes. Un backfill no re-descarga nada y no toca las demás particiones (ver
  ADR-011 y ADR-012).

### Ubicación y retención

Los archivos viven en `data/bronze/<fuente>/...`, fuera de git. Producción se
**sobrescribe por partición** en cada full refresh (se conserva el último estado,
particionado); el catálogo de pozos **acumula snapshots** por fecha de ingesta.
La asimetría es deliberada: guardar snapshots datados del dataset grande de
producción (144 MB por corrida) no se justifica, porque la fuente ya reemite las
correcciones dentro del archivo; el catálogo es chico y su historia es barata de
conservar.

## Consecuencias

**Positivas:**
- Bronze fiel al crudo (todo texto, sin transformar) y a la vez eficiente de leer
  y almacenar (parquet comprimido con esquema).
- El particionado de producción por `anio/mes`, derivado del landing, habilita el
  backfill de un mes puntual (reescribe solo esa partición, sin re-descargar) y
  lecturas selectivas por período desde Silver.
- El descarte de BOM evita el bug que ensuciaba el nombre de la primera columna,
  validado con tests.
- La separación crudo/limpieza deja claro el contrato con Silver: Bronze entrega
  el literal, Silver castea y normaliza.

**Negativas:**
- Todo como texto obliga a Silver a castear cada columna; el esquema tipado no se
  define en Bronze.
- La retención asimétrica (snapshots en pozos, sobrescritura en producción)
  implica que no hay historial de ingestas de producción: para reconstruir un
  estado pasado se depende de volver a la fuente.
- El landing guarda una copia completa extra del crudo en disco (además de las
  particiones), a cambio de no re-descargar en cada backfill.
- Parquet no es legible "a ojo" como un CSV; inspeccionarlo requiere una
  herramienta (pandas, DuckDB).
