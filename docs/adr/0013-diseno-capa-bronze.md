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

### Ubicación y retención

Los archivos viven en `data/bronze/<fuente>/...`, fuera de git. Producción se
**sobrescribe por partición** en cada full refresh (se conserva el último estado,
particionado); el catálogo de pozos **acumula snapshots** por fecha de ingesta.
La asimetría es deliberada: guardar snapshots datados del dataset grande de
producción (144 MB por corrida) no se justifica, porque la fuente ya reemite las
correcciones dentro del archivo; el catálogo es chico y su historia es barata de
conservar.
