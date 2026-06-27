# Título: ADR-034: Contrato del endpoint de predicción de producción

**Estado:** Propuesta

> Relacionado con [ADR-001](0001-framework-backend.md) (FastAPI), [ADR-010](0010-api-key-validation-strategy.md) (autenticación), [ADR-033](0033-serving-strategy.md) (estrategia de serving). Este ADR documenta el diseño del contrato HTTP del endpoint `POST /api/v1/predict`.

---

## Contexto

Se necesita un endpoint que exponga el modelo de producción (Rol 1, MLflow registry) a consumidores HTTP: dashboards, scripts de análisis y el equipo de operaciones. Hay que decidir:

1. Método HTTP: `GET` con query params vs `POST` con body.
2. Granularidad: predicción de un pozo a la vez vs batch (N pozos en un request).
3. Input: ¿qué identifica la predicción? ¿Hay que pasar features explícitas o solo la clave del pozo?
4. Output: ¿qué devuelve el endpoint además de la predicción?

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

---

## Decisión

**`POST /api/v1/predict`** con el siguiente contrato:

### Request
```json
{
  "idpozo": 507,
  "anio": 2026,
  "mes": 6
}
```
- `idpozo`: ID numérico del pozo (entero positivo).
- `anio`: año del mes a PREDECIR (t+1). Rango: 2000–2099.
- `mes`: mes a predecir (1-12, t+1).

La API busca en el feature store las features del mes t = (anio, mes) - 1 mes y las pasa al modelo.

### Response
```json
{
  "idpozo": 507,
  "anio": 2026,
  "mes": 6,
  "prod_pet_predicha": 423.7,
  "model_version": "3",
  "model_stage": "Production"
}
```
- `prod_pet_predicha`: producción predicha en m³, redondeada a 2 decimales.
- `model_version`: versión del modelo actualmente cargado en el servidor.
- `model_stage`: stage del modelo (`Production`).

### Códigos de respuesta
| Código | Condición |
|---|---|
| 200 | Predicción exitosa |
| 403 | API key inválida o ausente (middleware, antes de llegar al handler) |
| 404 | El pozo no tiene datos de features en el mes base (t-1) |
| 422 | Parámetros de entrada inválidos (Pydantic validation) |
| 503 | Modelo no disponible (MLflow sin versión Production o inalcanzable) |
| 429 | Rate limit excedido (mismo límite que el resto de los endpoints) |

### Autenticación y rate limiting
Hereda el middleware de API key (`X-API-Key`) y el rate limiter de slowapi (configurado via `RATE_LIMIT`) que aplica a todas las rutas `/api/`. No requiere cambios en la infraestructura de seguridad existente.

## Consecuencias

**Positivas:**
- Los consumidores del endpoint no necesitan conocer el feature store ni el modelo; solo necesitan el ID del pozo y el mes objetivo.
- El `model_version` en la respuesta permite auditar qué versión del modelo generó cada predicción, lo que es útil para debugging y reproducibilidad.
- El contrato es fácilmente extensible: se puede agregar `prod_gas_predicha`, `intervalo_confianza`, o un endpoint `/predict/batch` sin romper el contrato actual.

**Negativas:**
- El endpoint hace una consulta a Postgres (feature store) por cada request, lo que agrega latencia de DB. Para el volumen esperado (consultas puntuales, no bulk) esto es aceptable. Si el volumen crece, se puede agregar caché en memoria por (idpozo, anio, mes).
- La predición unitaria no es óptima para el batch scoring del backlog completo de pozos. Ese caso de uso debe usar el script de entrenamiento/scoring directo (`ml/baseline.py` o el futuro `train.py`), no la API REST.
