# Título: ADR-030: Plataforma de tracking de experimentos (MLflow)
**Estado:** Aceptada

## Contexto

La consigna de la Fase 3 exige una **plataforma de tracking de experimentos** que haga el entrenamiento **reproducible**: poder volver a un experimento anterior y reconstruir cómo se obtuvo un modelo. Concretamente se necesita registrar, por cada corrida (*run*):

- **Parámetros** del modelo y del experimento (algoritmo, hiperparámetros, definición del split, versión de datos).
- **Métricas** de evaluación: el campeón loguea **dev y test** (RMSE y R²; dev = train+val, `ml/registry.py`); los **baselines** loguean **val y test** (con MAE, `ml/baseline.py`).
- El **modelo entrenado** como artefacto (el `Pipeline` completo con preprocesamiento).
- Un **registry** para versionar modelos (v1, v2, …) y marcar el "campeón" con stages **Staging → Production**, que luego consume la API de inferencia.

Hay que elegir la herramienta que cumpla esto, en coherencia con la filosofía del resto del proyecto (open-source, self-hosted, datos on-premise; ver ADR-003).

## Análisis de Alternativas

1. **MLflow (open-source, self-hosted):** estándar de facto. Incluye Tracking + **Model Registry** + serving. Python-native, se integra con `scikit-learn` (`mlflow.sklearn.log_model`), corre con backend en SQLite (desarrollo) o **Postgres** (producción) y guarda artefactos en disco/objeto. Gratis y sin que los datos salgan de la infraestructura propia.
2. **Weights & Biases (W&B):** SaaS con una UI muy pulida y colaboración fuerte. Pero es un **servicio externo de pago**, los datos de experimentos **salen de la premisa** hacia la nube del proveedor, y ata el proyecto a una cuenta/cuota externa.
3. **Neptune / Comet:** equivalentes a W&B (SaaS gestionado), con las mismas objeciones de costo y datos fuera de premisa.
4. **Solución casera (loguear a CSV / tabla propia):** control total y cero dependencias, pero hay que **reinventar** el registro de runs, la comparación, el almacenamiento de artefactos y —sobre todo— el **model registry con stages**, que es un requisito. Alto costo de desarrollo para reconstruir algo que ya existe maduro.

## Decisión

Se decide usar **MLflow**, self-hosteado, por los siguientes motivos:

- **Coherencia con el proyecto:** igual que la elección de Prometheus/Grafana sobre DataDog/New Relic (ADR-003), se prioriza una herramienta **open-source y self-hosted** frente a un SaaS pago, manteniendo los datos dentro de la infraestructura propia.
- **Model Registry incluido:** cubre directamente el requisito de versionar modelos y promoverlos por stages (Staging → Production), sin sumar otra herramienta.
- **Integración con el stack existente:** Python-native, `mlflow.sklearn` con **logging manual** (`log_param`/`log_metric`/`log_model` en `ml/registry.py` y `ml/baseline.py`), backend en **Postgres** (ya presente en el proyecto) y artefactos en disco/almacenamiento de objetos. Se sirve por Docker, como el resto de los componentes.
- **Sin costo** y sin cuotas externas.
- **Camino de desarrollo a producción claro:** en local se usa un backend **SQLite** (`ml/config.py`) para iterar rápido; el despliegue "de verdad" (servidor de tracking sobre Postgres + Docker) se hace aparte, sin cambiar el código de los experimentos.

## Consecuencias

**Positivas:**
- Reproducibilidad del entrenamiento: cada run queda registrado con sus params, métricas, datos y modelo.
- El registry permite versionar y promover el modelo campeón, que la API de inferencia carga por stage (no por versión hardcodeada).
- Sin costo de licencias y con los datos on-premise.
- Mismo paradigma de desarrollo local → despliegue que ya usa el proyecto.

**Negativas / trade-offs:**
- A diferencia de un SaaS gestionado (W&B), hay que **operar y mantener** la infraestructura de MLflow (servidor de tracking, backend de base de datos, almacenamiento de artefactos).
- La UI de MLflow es **menos pulida** que la de W&B y sus capacidades de colaboración son más básicas.
- Requiere disciplina de configuración (backend, artifact store) para que los runs de todos los integrantes del equipo sean consistentes.

---

> Implementado en `ml/config.py` y `ml/tracking.py`. Relacionado: ADR-003 (mismo criterio open-source/self-hosted sobre SaaS), ADR-028 (diseño) y ADR-029 (baseline, que se loguea como run de comparación).
