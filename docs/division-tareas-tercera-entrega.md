**Distribución de roles para la Fase de Machine Learning**

## **Rol 1 — ML Engineer**

**Modelado \+ tracking de experimentos**

Es el dueño del modelo y de que el entrenamiento sea reproducible.

### **1.1 Definir el problema predictivo**

El primer paso es decidir qué se va a predecir. En este caso, lo más natural es trabajar sobre un **forecast de producción**, por ejemplo: predecir los m³ de petróleo o gas de un pozo para el próximo mes.

Esto tiene sentido porque ya existe el endpoint `/forecast`, que actualmente devuelve datos mock, y porque la capa Gold ya cuenta con la tabla `fact_produccion_mensual`.

Se debe definir:

* El **target**, es decir, la variable a predecir.  
* El **grano** del modelo: por ejemplo, por pozo y por mes, o por yacimiento.  
* La **métrica de evaluación**, como MAE o RMSE, para medir cuánto se equivoca el modelo en promedio.

### **1.2 Feature engineering**

Una feature es una variable de entrada del modelo. En esta etapa se definen qué variables se van a usar para entrenar.

Algunos ejemplos posibles:

* Producción de meses anteriores, usando lags.  
* Promedios móviles.  
* Antigüedad del pozo.  
* Tipo de yacimiento.  
* Operadora.

Cada feature debe quedar documentada indicando de qué columna de Gold sale y cómo se calcula. Esto se coordina con el Rol 2, que será quien materialice esas features en el feature store.

### **1.3 Entrenamiento y experimentación**

El Rol 1 debe escribir el script de entrenamiento, por ejemplo `train.py`, que siga el flujo:

leer features → entrenar modelo → evaluar resultados

También debe probar distintas configuraciones:

* Distintos algoritmos, como regresión lineal o modelos basados en árboles.  
* Distintos hiperparámetros.  
* Diferentes conjuntos de features.

Como se trata de una serie temporal, la separación entre train y test debe respetar el tiempo: se entrena con datos del pasado y se valida con datos futuros, evitando mezclar fechas.

### **1.4 Experiment tracking con MLflow**

Se debe montar MLflow como plataforma de tracking de experimentos.

El script `train.py` debe registrar automáticamente:

* Parámetros del modelo.  
* Métricas de evaluación.  
* Versión de los datos usados.  
* Modelo entrenado.

Esto permite mostrar en el video las métricas de distintos runs y garantiza reproducibilidad: poder volver a mirar un experimento anterior y reconstruir cómo se obtuvo ese modelo.

### **1.5 Model registry**

Se debe usar el registry de MLflow para versionar los modelos, por ejemplo `v1`, `v2`, etc.

También se debe marcar cuál es el modelo campeón que pasa a producción, usando stages como:

Staging → Production

El criterio de promoción debe quedar definido: un modelo nuevo pasa a producción si mejora la métrica del modelo actual. Este modelo será consumido por el Rol 3 para servir predicciones.

### **ADRs sugeridos**

* Plataforma de tracking: MLflow vs Weights & Biases vs otras alternativas.  
* Tipo de modelo o algoritmo elegido y justificación.

### **Requisitos que cubre**

* Tracking reproducible del entrenamiento.  
* Modelo predictivo.

---

## **Rol 2 — Feature Store \+ Orquestación del reentrenamiento**

Es el puente entre datos y features. También es responsable de que el reentrenamiento sea automático, recurrente y reproducible por día. Reutiliza fuertemente lo construido en Fase 2 con Dagster y dbt.

### **2.1 Feature pipeline**

El Rol 2 toma la capa Gold del Data Warehouse y la transforma en la tabla de features definida junto con el Rol 1\.

Esto puede implementarse como:

* Un modelo nuevo de dbt.  
* Un asset de Dagster.

Lo importante es que el cálculo quede versionado, repetible y no se haga manualmente.

### **2.2 Feature store**

Las features deben persistirse en un feature store. Esto es un requisito no funcional explícito: las mismas features deben servir tanto para entrenamiento como para inferencia.

Esto evita el **training-serving skew**, que ocurre cuando las features usadas para entrenar se calculan distinto a las usadas en producción.

Una opción razonable para comenzar es usar un feature store offline, por ejemplo una tabla en el Postgres existente. Luego se puede evaluar si hace falta una herramienta más específica, como Feast.

El Rol 2 debe definir el esquema del store y publicarlo como contrato para los otros roles.

### **2.3 Orquestación del retrain con Dagster**

Se debe crear un job de reentrenamiento que encadene:

refrescar features → ejecutar train.py → registrar resultado en MLflow

Este job debe ser parametrizable por fecha usando partitions en Dagster. Eso permite pedir, por ejemplo: “reentrenar como si fuera el día X”.

Esto cubre el requisito de reproceso de entrenamiento para un día dado.

### **2.4 Reentrenamiento recurrente y automático**

Se debe configurar un schedule o sensor en Dagster para que el reentrenamiento corra automáticamente.

El trigger puede ser:

* Por calendario, por ejemplo diario o semanal.  
* Por llegada de nuevos datos.

Esto cubre el requisito de entrenamiento recurrente y automático.

### **2.5 Backfill y reproceso**

Debe quedar documentado en un runbook cómo reprocesar el entrenamiento para un día o rango de días pasado, por ejemplo si una fuente corrige datos históricos.

### **ADRs sugeridos**

* Feature store: tabla en Postgres vs Feast vs otra alternativa.  
* Estrategia de orquestación del retrain: parametrización por día y criterio de disparo.

### **Requisitos que cubre**

* Feature store.  
* Orquestación con reproceso por día.  
* Retrain recurrente y automático.

---

## **Rol 3 — Serving API \+ CI/CD \+ Deploy del modelo**

Es el dueño de exponer el modelo al usuario y automatizar el despliegue.

### **3.1 API de predicciones**

Se debe agregar un endpoint de inferencia a la FastAPI existente, por ejemplo:

POST /api/v1/predict

Este endpoint recibe un input, como un pozo y un mes, y devuelve la predicción correspondiente.

Debe mantenerse la coherencia con la API actual:

* Misma autenticación por API key.  
* Mismos schemas Pydantic.  
* Mismo monitoreo con Prometheus/Grafana.

También debe definirse el contrato de la API:

* Qué recibe.  
* Qué devuelve.  
* Qué ocurre ante inputs inválidos.

### **3.2 Carga del modelo desde el registry**

Durante la inferencia, la API debe traer el modelo marcado como `Production` en el MLflow Model Registry.

No se debe hardcodear una versión específica del modelo. Además, la API debe saber qué versión está sirviendo, lo cual es útil para debugging y para mostrar en el video.

### **3.3 Inferencia con feature store**

Al predecir, la API debe leer las features desde el feature store definido por el Rol 2\.

La API no debería recalcular las features por su cuenta, porque eso podría generar diferencias entre entrenamiento e inferencia.

### **3.4 Despliegue automático del modelo**

Cuando un modelo nuevo se promueve a `Production`, la API debe empezar a servirlo sin intervención manual.

Esto puede hacerse mediante:

* Recarga automática del modelo.  
* Redeploy automático del contenedor.  
* Mecanismo de polling sobre el registry.

Esto cubre el requisito de despliegue recurrente y automático de modelos.

### **3.5 CI/CD de los pipelines**

Se debe extender el GitHub Actions existente para incluir los nuevos componentes de ML:

* Entrenamiento.  
* Tests del feature pipeline.  
* Tests del endpoint de predicción.  
* Build de la imagen de serving.  
* Deploy automático.

Esto cubre el requisito explícito de despliegue vía CI/CD.

### **3.6 Infraestructura con Docker**

Se debe extender el `docker-compose` para levantar los nuevos servicios junto con los existentes.

Por ejemplo:

* MLflow.  
* Servicio de serving.  
* Base de datos o storage asociado al feature store, si corresponde.

### **ADRs sugeridos**

* Estrategia de serving y despliegue automático del modelo.  
* Contrato y diseño de la API de predicción.

### **Requisitos que cubre**

* API REST.  
* Uso del feature store en inferencia.  
* Despliegue automático.  
* CI/CD.

---

## **Trabajo transversal**

### **README**

El README debe explicar la arquitectura completa. Cada rol escribe su sección correspondiente.

Debe incluir:

* Problema predictivo.  
* Feature store.  
* Pipeline de entrenamiento.  
* Tracking con MLflow.  
* Registry de modelos.  
* API de inferencia.  
* CI/CD y despliegue.

### **Video de 5 a 10 minutos**

Cada rol debe demostrar su parte:

* Rol 1: runs en MLflow y métricas de entrenamiento.  
* Rol 2: trigger de un retrain o reproceso por fecha.  
* Rol 3: llamadas a la API mostrando predicciones y versión del modelo servido.

### **ADRs**

Cada ADR debe comparar alternativas. Si no compara alternativas, se considera inválido.

Ejemplo de estructura mínima:

Contexto  
Alternativas consideradas  
Decisión tomada  
Justificación  
Consecuencias

---

## **Nota de coordinación**

El Rol 2 desbloquea a los otros dos, porque define el esquema del feature store. Por eso conviene que arranque primero, o que los tres roles acuerden ese esquema en la primera reunión.

Una buena primera decisión conjunta sería definir concretamente qué features tendrá el store y de qué tablas Gold saldrá cada una.

