# Título: ADR-035: Contrato del endpoint de predicción de producción

**Estado:** Reemplazado por [ADR-044](0044-forecast-recursivo.md)

> **Reemplazado (ADR-044):** el endpoint `POST /api/v1/predict` (predicción de un mes) se **retiró**. El `GET /api/v1/forecast` recursivo lo **subsume**: un rango de un solo mes equivale a la predicción de un mes, y el parámetro `target` (petróleo/gas) pasa a `/forecast` como opcional. Este ADR queda como registro del diseño original del paso unitario, cuya lógica (leer features → pipeline → valor) vive ahora dentro del forecast.

> Relacionado con [ADR-001](0001-framework-backend.md) (FastAPI), [ADR-010](0010-api-key-validation-strategy.md) (autenticación), [ADR-038](0038-serving-strategy.md) (estrategia de serving), [ADR-042](0042-modelo-prediccion-gas.md) (segundo modelo, gas) y [ADR-044](0044-forecast-recursivo.md) (que lo reemplaza). Este ADR documentaba el diseño del contrato HTTP del endpoint `POST /api/v1/predict`.

---

## Contexto

Se necesita un endpoint que exponga el modelo de producción (del MLflow registry) a consumidores HTTP: dashboards, scripts de análisis y el equipo de operaciones. Hay que decidir:

1. Método HTTP: `GET` con query params vs `POST` con body.
2. Granularidad: predicción de un pozo a la vez vs batch (N pozos en un request).
3. Input: ¿qué identifica la predicción? ¿Hay que pasar features explícitas o solo la clave del pozo?
4. Output: ¿qué devuelve el endpoint además de la predicción?
5. **Cómo exponer los dos targets** (petróleo y gas, ADR-042): la Fase 3 pasó a tener **dos modelos**, uno por producción. El ADR-042 dejó explícitamente a este ADR decidir cómo elige el cliente cuál usar.

---

## Análisis de Alternativas

### Input: features explícitas vs clave de lookup

**Alternativa X — El cliente pasa las features directamente:**
```json
{ "lag1": 500.0, "lag2": 480.0, ... }
```
El cliente es responsable de computar las features antes de llamar al endpoint.

**Descartada:** duplica la lógica de feature engineering en cada cliente. Si el feature store cambia, hay que actualizar todos los consumidores. Va contra el principio de Single Source of Truth.

**Alternativa Y — El cliente pasa solo la clave del pozo y el mes (seleccionada):**
```json
{ "idpozo": 507, "anio": 2026, "mes": 6 }
```
La API hace el lookup en el feature store internamente.

**Ventaja:** desacopla completamente a los consumidores del feature engineering. **Decisión adoptada.**

### Método HTTP

**GET con query params** (`/predict?idpozo=507&anio=2026&mes=6`): semánticamente correcto para lecturas idempotentes. Pero la predicción es computacionalmente no trivial (descarga de features de DB + inferencia), por lo que el cacheo automático de GET en proxies intermedios no es deseable sin control explícito. Además, si en el futuro se extiende a batch (lista de pozos), los query params se vuelven incómodos.

**POST con body** (seleccionado): semánticamente indica una operación "disparada" (el cliente solicita que se calcule algo). No se cachea por defecto. El body en JSON es fácilmente extensible a batch. Coherente con el diseño de otros endpoints de ML en APIs REST modernas.

### Granularidad: unitario vs batch

**Batch** (`{ "pozos": [{ "idpozo": 507, "anio": 2026, "mes": 6 }, ...] }`): permite amortizar el costo de cargar el modelo entre N predicciones.

**Unitario** (seleccionado para la entrega): más simple, más fácil de testear, y suficiente para los casos de uso actuales (consulta de un pozo a la vez desde Metabase o scripts). El body `PredictRequest` puede extenderse a un array en el futuro sin romper el contrato si se agrega un endpoint `/predict/batch`.

### Cómo exponer el target (petróleo vs gas)

Con dos modelos (ADR-042), el cliente tiene que poder elegir qué producción predecir. Alternativas:

**Alternativa 1 — Dos endpoints separados** (`/predict/petroleo`, `/predict/gas`): explícito y auto-documentado, pero **duplica la ruta y su lógica** (mismo request, mismo lookup, misma autenticación) y obliga a tocar el router cada vez que se agregue un target nuevo (ADR-036 contempla `table_for(target)` y más targets a futuro).

**Alternativa 2 — Predecir ambos targets en una sola respuesta** (`{prod_pet_predicha, prod_gas_predicha}`): cómodo para el cliente, pero **acopla los dos modelos** en un request (si uno no tiene versión `Production` o el pozo no está en su universo —el gasífero es más amplio, ADR-042— el request entero falla o queda a medias) y **desperdicia** cómputo/latencia cuando el cliente solo quiere uno.

**Alternativa 3 — Un parámetro `target` en el body (seleccionada):** `target ∈ {prod_pet, prod_gas}`, por defecto `prod_pet` (compatible con los requests previos al segundo modelo). Una sola ruta enruta a la tabla del store y al modelo del registry de ese target. **Ventajas:** una única ruta y contrato; agregar un target futuro es sumar un valor al enum, no una ruta nueva; cada request resuelve un solo modelo (fallas y universos independientes por target); Pydantic valida el valor (un target inválido devuelve `422`). Es coherente con el diseño unitario de arriba y con el feature store parametrizado por target del Rol 2.

---

## Decisión

**`POST /api/v1/predict`** con el siguiente contrato:

### Request
```json
{
  "idpozo": 507,
  "anio": 2026,
  "mes": 6,
  "target": "prod_pet"
}
```
- `idpozo`: ID numérico del pozo (entero positivo).
- `anio`: año del mes a PREDECIR (t+1). Rango: 2000–2099.
- `mes`: mes a predecir (1-12, t+1).
- `target`: `prod_pet` (petróleo) o `prod_gas` (gas). **Opcional**, por defecto `prod_pet` (ADR-042).

La API busca en el feature store —en la **tabla del target** (ADR-036)— las features del mes t = (anio, mes) - 1 mes y las pasa al modelo `Production` **de ese target**.

### Response
```json
{
  "idpozo": 507,
  "anio": 2026,
  "mes": 6,
  "target": "prod_pet",
  "prediccion": 423.7,
  "unidad": "m3",
  "model_name": "produccion-forecast",
  "model_version": "3",
  "model_stage": "Production"
}
```
- `target`: producción predicha (`prod_pet` / `prod_gas`), eco del request.
- `prediccion`: producción predicha del target en m³, redondeada a 2 decimales. Es un campo **genérico** (no `prod_pet_predicha`) porque el mismo contrato sirve a ambos targets.
- `unidad`: unidad de la predicción (`m3`).
- `model_name`: modelo del registry que sirvió la predicción (`produccion-forecast` / `produccion-forecast-gas`) — deja explícito qué modelo respondió.
- `model_version`: versión del modelo actualmente cargado en el servidor.
- `model_stage`: stage del modelo (`Production`).

### Códigos de respuesta
| Código | Condición |
|---|---|
| 200 | Predicción exitosa |
| 403 | API key inválida o ausente (middleware, antes de llegar al handler) |
| 404 | El pozo no tiene datos de features en el mes base (t-1) en la tabla del target |
| 422 | Parámetros de entrada inválidos, incluye un `target` fuera de `{prod_pet, prod_gas}` (Pydantic validation) |
| 503 | Modelo del target no disponible (MLflow sin versión Production o inalcanzable) |
| 429 | Rate limit excedido (mismo límite que el resto de los endpoints) |

### Autenticación y rate limiting
Hereda el middleware de API key (`X-API-Key`) y el rate limiter de slowapi (configurado via `RATE_LIMIT`) que aplica a todas las rutas `/api/`. No requiere cambios en la infraestructura de seguridad existente.

## Consecuencias

**Positivas:**
- Los consumidores del endpoint no necesitan conocer el feature store ni el modelo; solo necesitan el ID del pozo y el mes objetivo.
- El `model_name` + `model_version` en la respuesta permiten auditar qué modelo y versión generó cada predicción, lo que es útil para debugging y reproducibilidad.
- El contrato es fácilmente extensible: un target nuevo es un valor más del enum `target` (una tabla y un modelo más, sin ruta nueva); y `intervalo_confianza` o un endpoint `/predict/batch` se pueden agregar sin romper el contrato actual.

**Negativas:**
- El endpoint hace una consulta a Postgres (feature store) por cada request, lo que agrega latencia de DB. Para el volumen esperado (consultas puntuales, no bulk) esto es aceptable. Si el volumen crece, se puede agregar caché en memoria por (idpozo, anio, mes).
- La predición unitaria no es óptima para el batch scoring del backlog completo de pozos. Ese caso de uso debe usar el script de entrenamiento/scoring directo (`ml/train.py` / `ml/baseline.py`), no la API REST.
