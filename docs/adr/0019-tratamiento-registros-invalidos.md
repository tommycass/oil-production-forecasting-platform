# Título: ADR-019: Tratamiento de registros inválidos (cuarentena vs bloqueo)

**Estado:** Aceptado

## Contexto

Al correr el pipeline contra el **dataset real completo** (≈406 mil filas de producción
mensual), el gate de Data Quality (ADR-016) detectó **3 filas con producción negativa**
(`prod_pet`/`prod_gas` < 0). Son valores **físicamente imposibles** (no se puede producir gas o
petróleo negativo) y **no están marcados como `rectificado`**: son ruido/errores del dato
público de origen (datos.gob.ar), no correcciones legítimas.

El ADR-016 había clasificado "producción no negativa" como check **bloqueante**
(`severity: error`), lo que dejaba el pipeline **frenado**: con esas 3 filas, Gold quedaba sin
materializar (SKIP). Pero el problema es irreparable en origen — A baja el CSV **tal cual**
(Bronze es crudo inmutable, ADR-013) y no controla la fuente pública — así que un bloqueo duro
dejaría el DW **permanentemente sin actualizarse** por 3 filas en 406 mil. Hay que decidir cómo
trata Silver estos registros para que **Gold siga siendo confiable** sin frenar todo el flujo.

La limpieza es responsabilidad de la capa **Silver** (ADR-014), así que la decisión vive ahí.

### Evaluación de alternativas

| Criterio | Bloqueo duro (`error` frena Gold) | Clamp a 0 (`greatest(x,0)`) | Exclusión silenciosa (`where x>=0`) | Degradar a `warn` | **Cuarentena (tabla de rechazos)** |
|---|---|---|---|---|---|
| Gold se actualiza pese a la suciedad | ❌ no | ✅ sí | ✅ sí | ✅ sí | ✅ sí |
| No contamina las sumas de negocio | ✅ (no hay Gold) | ✅ (0 es neutro) | ✅ (se excluye) | ❌ deja negativos en Gold | ✅ (se excluye) |
| Preserva el dato real / no lo falsea | ✅ | ❌ inventa un 0 | ✅ | ✅ | ✅ |
| Reconciliación de conteos (bronze = silver + descartes) | n/a | ✅ (no se pierde fila) | ❌ filas desaparecen sin rastro | ✅ | ✅ (descartes auditables) |
| Auditabilidad / por qué se excluyó | n/a | ❌ se enmascara | ❌ nula | ⚠️ solo en logs de la corrida | ✅ tabla con motivo + timestamp |
| Pipeline desatendido (objetivo de la fase) | ❌ frena | ✅ | ✅ | ✅ | ✅ |
| Coordinar fix en origen con A | — (no viable: fuente pública) | — | — | — | — |

> "Corregir en origen / coordinar con A" se descarta de entrada: la suciedad está en el CSV
> público y Bronze debe preservarlo crudo; no es algo que A pueda ni deba arreglar.

## Decisión

Adoptamos el **patrón de cuarentena (quarantine / dead-letter)** para las filas que fallan una
**regla de validez dura** (medida físicamente imposible, p. ej. producción/inyección/tef
negativa):

- `silver_produccion` contiene **solo filas válidas**.
- Las filas inválidas se **desvían** a `dq.silver_produccion_rechazos` con su `motivo_rechazo` y
  un timestamp, en lugar de dropearlas en silencio o falsearlas.
- La lógica de tipado/dedup es compartida (modelo efímero `silver_produccion_vigente`, DRY) y
  ahí se calcula el flag de rechazo; los dos modelos (limpio y cuarentena) se derivan de él.
- El check de no-negatividad sobre `silver_produccion` pasa a ser un **invariante
  post-limpieza**: ahora siempre debe dar verde; si alguna vez fallara, significaría que la
  cuarentena se rompió → ahí sí bloquea, legítimamente.

Esto refina la clasificación de severidad del ADR-016: distinguimos **invariantes de
integridad** (unicidad de PK, no-nulos de claves, integridad referencial fact→dim), que **sí
bloquean** porque su fallo implica un bug *nuestro*, de la **suciedad irreparable de origen**,
que se **cuarentena**.

### Por qué cuarentena sobre las otras

- **Sobre bloqueo duro:** bloquear el DW entero por 3 filas irreparables (0,0007 %) es
  desproporcionado y contradice el objetivo de un pipeline desatendido.
- **Sobre clamp a 0:** inventar un 0 falsea el dato y enmascara que la fuente tiene errores;
  preferimos no fabricar valores.
- **Sobre exclusión silenciosa:** filtrar sin registro rompe la reconciliación de conteos y deja
  a cualquiera preguntándose "por qué faltan filas". La cuarentena excluye **igual de limpio**
  pero deja rastro auditable.
- **Sobre degradar a `warn`:** dejaría producción negativa llegar a Gold y contaminar
  agregaciones (aunque marginalmente), traicionando la garantía de confiabilidad de Gold.

## Consecuencias

**Positivas:**
- Gold se mantiene limpio y actualizable; el pipeline no se frena por suciedad de origen.
- Trazabilidad total: `bronze = silver + rechazos` se reconcilia, y cada exclusión queda con su
  motivo en `dq.silver_produccion_rechazos` (consumible por BI/gobierno como señal de calidad).
- Coherente con Medallion (Bronze crudo intacto, Silver limpia) y con el `dq` schema existente.

**Negativas:**
- Un modelo más para mantener (`silver_produccion_vigente` efímero + la tabla de rechazos).
- La regla de validez ("qué es rechazo") es criterio explícito que hay que mantener y revisar.
- Las filas en cuarentena no se reprocesan automáticamente: si la fuente las corrige en una
  ingesta futura, vuelven a evaluarse en el próximo `dbt build` (full-refresh), lo cual es el
  comportamiento deseado, pero hay que tenerlo presente.

## Decisiones Técnicas Posteriores

- **Retención de `dq.silver_produccion_rechazos`:** como el resto de tablas `dq.*`, definir
  rotación/truncado si crece (hoy es full-refresh por corrida, así que refleja el estado actual).
- **Alerta por volumen:** evaluar un check `warn` sobre el conteo de rechazos para avisar si un
  mes trae una cantidad anómala de filas inválidas (señal de degradación de la fuente).
- **Exposición en gobierno:** coordinar con C para surfacear la tabla de rechazos y `dq.dq_results`
  en DataHub/Metabase como señales de calidad a nivel tabla.
