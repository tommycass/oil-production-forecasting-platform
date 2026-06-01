# data_pipeline

Pipeline de datos de la Fase 2 (Integración de Datos). Cubre la **ingesta** desde
las fuentes públicas de datos.gob.ar y la persistencia del crudo en la **capa
Bronze** de la arquitectura Medallion.

> Zona del Data Engineer: `Data Sources → Extracción → Bronze`.
> Las capas Silver/Gold y el modelo estrella viven aguas abajo (Analytics Engineer).

## Estructura

```
data_pipeline/
  config.py              URLs de las fuentes + ruta de Bronze (definidas una sola vez)
  extraction/
    extract_pozos.py     Listado de pozos      → Bronze  (carga full refresh)
    extract_produccion.py Producción por pozo  → Bronze  (carga merge/upsert)
```

Los datos crudos se escriben en `data/bronze/` (fuera de este paquete y
gitignoreado: ver `data/.gitignore`). Solo se versiona la estructura, nunca los
archivos de datos.

## Fuentes

| Fuente | Clave | Grano | Estrategia de carga |
|--------|-------|-------|---------------------|
| Producción de pozos (no convencional) | `idpozo` | idpozo + anio + mes | merge/upsert (la fuente corrige meses) |
| Listado de pozos por operadora | `idpozo` | un pozo por fila | full refresh (catálogo chico) |

## Estado

Estructura inicial. La lógica de extracción, el orquestador (ADR-011) y el
procedimiento de backfill están pendientes — los stubs marcan dónde van.
