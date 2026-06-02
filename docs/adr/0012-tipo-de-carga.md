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
