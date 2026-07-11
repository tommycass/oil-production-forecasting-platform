# Título: ADR-037: Estrategia de carga y actualización del modelo en la API de inferencia

**Estado:** Aceptada

> Relacionado con [ADR-030](0030-plataforma-tracking-experimentos.md) (MLflow como registry) y [ADR-042](0042-forecast-recursivo.md) (`/forecast`, el endpoint que sirve el modelo). Este ADR decide cómo la API carga el modelo desde el registry y cómo detecta y aplica nuevas versiones sin downtime.

---

## Contexto

La API de inferencia necesita:

1. **Cargar el modelo al arrancar** desde el MLflow Model Registry (stage `Production`).
2. **Actualizar el modelo automáticamente** cuando el Rol 1 promueve una nueva versión a `Production`, sin requerir un redeploy ni reinicio del contenedor de la API.
3. **Ser resiliente a la indisponibilidad de MLflow**: si MLflow no está disponible al arrancar (por ejemplo, en desarrollo sin el perfil `ml` activo), la API debe arrancar de todas formas y retornar `503` solo en el endpoint `/forecast`, no en `/health` ni en `/wells`.

El ciclo de reentrenamiento es **mensual** (ADR-040: schedule el día 6 + sensor por datos nuevos), no continuo, por lo que la latencia de detección de una nueva versión puede ser del orden de minutos, no de segundos.

---

## Análisis de Alternativas

### Alternativa A — Redeploy del contenedor al registrar un nuevo modelo

Cuando el Rol 1 promueve un modelo a Production, dispara un nuevo deploy de la API vía GitHub Actions. La API arranca con la nueva versión.

**Descartada:** introduce acoplamiento entre el ciclo de ML y el CI/CD de la API. El Rol 1 necesitaría acceso al pipeline de deploy, y el endpoint estaría caído durante el redeploy (~30s). No justificado dado el ciclo de actualización lento.

### Alternativa B — Webhook desde MLflow al registrar modelo

MLflow (versión ≥ 2.0) soporta model registry webhooks que notifican transiciones de stage. La API expone un endpoint interno que, al recibir el webhook, recarga el modelo.

**Descartada:** requiere que la API sea accesible desde el servidor MLflow (firewall, VPC, URL pública del endpoint interno), lo que complica la configuración de red. Además, los webhooks de MLflow tienen garantías at-most-once, por lo que requieren lógica adicional de reconciliación. El overhead de implementación no se justifica para el ciclo lento.

### Alternativa C — Polling en background thread (seleccionada)

Un hilo daemon dentro del proceso de la API consulta el MLflow registry cada N segundos. Si detecta una nueva versión en `Production`, la descarga y la reemplaza en memoria de forma thread-safe.

**Ventajas:**
- **Sin dependencias externas:** no requiere webhooks ni configuración de red adicional.
- **Thread-safe:** el hilo usa un `threading.Lock` para reemplazar el modelo sin race conditions.
- **Silencia errores del poller:** si MLflow no está disponible en un ciclo, el hilo loguea un warning y reintenta en el siguiente ciclo, sin afectar las predicciones en curso.
- **Latencia aceptable:** el ciclo de reentrenamiento es mensual; 5 minutos de latencia de detección no tiene impacto operativo.

**Desventajas:**
- El hilo no se puede testear end-to-end en tests unitarios sin sleeps; se prueba la lógica de `load()` en aislamiento.
- En un despliegue multi-réplica (múltiples instancias de la API) cada instancia polea independientemente, lo que es correcto pero implica N conexiones al MLflow server. Para el escenario actual (una instancia por EC2) esto no es problema.

---

## Decisión

**Polling en background thread**, con las siguientes configuraciones:

| Parámetro | Default | Env var |
|---|---|---|
| Intervalo de polling | 300 segundos (5 min) | `MODEL_POLL_INTERVAL_SECONDS` |
| Nombre del modelo (petróleo) | `produccion-forecast` | `MLFLOW_MODEL_NAME` |
| Nombre del modelo (gas) | `produccion-forecast-gas` | `MLFLOW_MODEL_NAME_GAS` |
| URI del tracking server | `http://localhost:5000` | `MLFLOW_TRACKING_URI` |

**Dos modelos, un loader por target (ADR-039).** El código expone un registro `MODEL_LOADERS` indexado por target (`api/app/services/model_loader.py::MODEL_NAME_BY_TARGET`): un `ModelLoader` para `prod_pet` y otro para `prod_gas`, cada uno con su nombre de registry y su polling independiente. El arranque es **degradado por target**: si falla la carga de uno, el otro sigue sirviendo y solo ese `target` de `/forecast` responde `503`.

**Comportamiento al arrancar:**
- Si `load()` tiene éxito: modelo disponible de inmediato, poller iniciado.
- Si falla (MLflow sin modelo Production, o MLflow no disponible): API arranca igual, `/forecast` retorna `503`, el poller sigue intentando cada 5 minutos.

**Thread safety:** `threading.Lock` protege `self._model` y `self._version`. El hilo solo escribe; los requests solo leen. El lock se toma por el tiempo mínimo necesario (solo el swap de referencia, no la descarga del modelo).

## Consecuencias

**Positivas:**
- Auto-actualización sin downtime: cuando el Rol 1 promueve modelo v2 a Production, la API lo detecta y lo usa en el siguiente ciclo de polling (~5 min).
- La API de inferencia es independiente del ciclo de CI/CD de ML.
- Resiliente a indisponibilidad transitoria de MLflow.

**Negativas:**
- Ventana de hasta 5 minutos entre la promoción del modelo y su disponibilidad en la API (aceptable para el ciclo mensual de reentrenamiento).
- En caso de downgrade (un modelo v3 que falla se retira de Production), el poller detecta que no hay versión Production y loguea un warning, pero la API sigue sirviendo el modelo v3 ya cargado en memoria hasta el próximo restart. Mitigación: agregar manejo explícito del caso "sin versión Production" para limpiar `self._model` — considerado fuera de scope para la entrega.
