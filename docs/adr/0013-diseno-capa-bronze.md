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
