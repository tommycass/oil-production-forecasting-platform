# Guion + Estructura de Slides — Video Demo Fase 3

**Proyecto:** Plataforma de Datos — Producción Hidrocarburífera
**Fase 3:** Modelo Predictivo de Producción
**Duración objetivo:** ~9 minutos (dentro del rango 5–10 de la consigna) → **~6 min de slides · ~3 min de demo**
**Entrega:** 11 de julio de 2026

## Integrantes y roles en Fase 3

| Integrante | Rol | Zona del sistema |
|---|---|---|
| **Micol** | Rol 1 — ML Engineer | Problema de modelado, features, selección, forecast recursivo |
| **Valentino** | Rol 2 — Feature Store + Orquestación | Feature store, retrain (Dagster), precómputo |
| **Tomás** | Rol 3 — Serving + Infra | MLflow (server + registry), API `/forecast`, CI/CD, entorno local |

## Convenciones del guion

- Cada bloque bajo un slide es **el texto a leer tal cual**.
- Cuando hablan dos integrantes en un mismo slide, el segundo **agrega contenido propio** — no hace preguntas ni repite.
- Los aportes cruzados (Micol hablando de infra, Tomás de modelado) son deliberados: muestran que **los tres conocemos el sistema completo**.
- `[VISUAL]` indica qué mostrar. Varias remiten a las capturas de `docs/demo_adenda3/`.
- Los números en negrita están **verificados contra los ADRs**. No usar los del README de la demo, que vienen de una corrida anterior y difieren en decimales.

---

## Cobertura de la consigna

Chequeo explícito antes de grabar. Cada requisito de la adenda tiene un slide donde se lo nombra.

| # | Requisito de la adenda | Dónde se cubre |
|---|---|---|
| 1 | El servicio DEBE exponer una API REST | Slide 10 |
| 2 | Plataforma de tracking que haga el entrenamiento **reproducible** | Slide 7 |
| 3 | Features persistidas en un **feature store usado en inferencia** | Slides 4 y 10 |
| 4 | Arquitectura completa descripta **en el README.md** | Slide 2 (se nombra explícitamente) |
| 5 | Orquestador que permita **repetir el entrenamiento para un día dado** | Slide 9 |
| 6 | Entrenamiento y despliegue **recurrente y automático** | Slides 8 y 9 |
| 7 | Pipelines de procesamiento desplegados vía **CI/CD** | Slide 11 |
| 8 | ADRs que **comparen alternativas** (los meramente descriptivos son inválidos) | Transversal + Slide 13 |
| 9 | Demo: métricas en distintos runs, llamadas a la API en distintas condiciones, **trigger de un retrain** | Slide 12 |

---

## SLIDE 1 — Portada

**[VISUAL]** Título "Plataforma de Datos — Producción Hidrocarburífera", subtítulo "Fase 3: Modelo Predictivo de Producción". Nombres del equipo. Logos del stack: scikit-learn · MLflow · Dagster · FastAPI · PostgreSQL · Docker.

> **[Micol]:** Somos Micol, Valentino y Tomás. En esta Fase 3 le sumamos machine learning a la plataforma que veníamos construyendo. El objetivo concreto: pronosticar la producción de petróleo y de gas del mes siguiente, para cada pozo, y servirlo por la API.
>
> Pero el modelo es la parte fácil. Lo que esta fase realmente construye es todo lo que lo rodea: que el entrenamiento sea reproducible y quede trackeado, que las features estén persistidas en un feature store que también se use en inferencia, que el reentrenamiento esté orquestado y se pueda repetir para un día puntual del pasado, y que la promoción a producción tenga un criterio explícito en vez de pisar el modelo a ciegas.
>
> Vamos a contar las decisiones, las alternativas que descartamos y por qué, y los hallazgos que nos cambiaron el diseño sobre la marcha.

> **[Tomás]:** Una aclaración de contexto. Esta entrega la desarrollamos y la corremos **íntegramente en local con Docker**, sin las instancias de AWS, tal como se habilitó para esta fase. El feature store, MLflow, el retrain y la API que van a ver levantan con `docker compose` en cualquier máquina. El código es el mismo que corría contra RDS y EC2: lo único que cambia son variables de entorno.

---

## SLIDE 2 — Arquitectura de la Fase 3

**[VISUAL]** Diagrama de flujo horizontal:

```
Fase 2 (DW Medallion)          Fase 3
─────────────────────          ──────────────────────────────────────────
bronze.produccion  ──►  Feature Store (Postgres, features.*)
                              │              │
                              ▼              ▼
                        Entrenamiento    Inferencia
                          (ml/)           (API)
                              │              ▲
                              ▼              │
                        MLflow ──────────────┘
                   (tracking + model registry)

        Retrain orquestado (Dagster):
        features_refrescadas → modelo_reentrenado → forecast_precomputado
```

Footer con el stack y los puertos.

> **[Tomás]:** La Fase 3 se apoya sobre la plataforma de datos de la Fase 2. La cadena es esta: desde el data warehouse construimos un **feature store**, que es la fuente única de features. Sobre ese store entrena el modelo, y cada corrida queda registrada en **MLflow**, que hace de plataforma de tracking de experimentos y de model registry. Del otro lado, la API carga desde ese registry el modelo que está en producción y sirve el pronóstico. Y todo el ciclo —refrescar el store, reentrenar, recalcular— está orquestado como un job de **Dagster**, el mismo orquestador de la Fase 2.
>
> La arquitectura completa de la solución está descripta en el **README del repositorio**, con el árbol de componentes, el diagrama de capas y una sección dedicada a Machine Learning. Ahí está el detalle que acá resumimos en un slide.

> **[Valentino]:** La idea de fondo es que ninguna pieza esté hardcodeada contra otra. El feature store, el entrenamiento, el registry y el serving están conectados como **contratos**, igual que hicimos con las capas Medallion en la Fase 2. Eso es lo que nos permite reentrenar y promover un modelo nuevo **sin tocar una línea del código de la API**.

---

## SLIDE 3 — El problema de modelado y el hilo conductor de la fase

**[VISUAL]** Línea de tiempo con tres tramos coloreados:

```
train                    val              test
2006-01 ──────► 2023-07 │ ──► 2024-11 │ ──► 2026-04
   65,0%                  15,6%          19,5%
```

Al costado, un callout grande: **"El leakage es el enemigo de toda la fase"** con cuatro viñetas: temporal · de selección · de vocabulario · entre folds.

> **[Micol]:** Antes del modelo hay que definir el problema. Predecimos la producción del **mes siguiente** —lo que llamamos t más uno— por pozo, para petróleo y gas **por separado**, porque son dos universos distintos de pozos. Cada fila del dataset es un par pozo-mes.
>
> Descartamos dos encuadres alternativos. **Forecasting clásico por serie**, tipo ARIMA o Prophet: modela bien cada serie, pero **no escala a más de tres mil pozos** y no aprovecha los atributos del pozo ni el aprendizaje cruzado entre pozos. Y **modelos secuenciales tipo LSTM**: potentes, pero con un costo de datos y cómputo desproporcionado para el alcance del trabajo. Elegimos una **regresión tabular global**: un solo modelo que aprende de todos los pozos a la vez.

> **[Micol]:** La decisión más importante es el **split temporal**. En vez de mezclar filas al azar, entrenamos con los meses más viejos y validamos y testeamos con los más nuevos, con un corte global por fecha: todos los pozos comparten el mismo límite temporal, para que nunca se use el futuro de un pozo al predecir otro.
>
> Y acá está el hilo conductor de toda la fase: **el leakage aparece en cuatro formas distintas, y cada una tiene su propio ADR**. El leakage temporal, que resuelve el split. El **leakage de selección**: si definís el universo de pozos petroleros mirando todo el histórico, un pozo que recién produce en 2025 entra a train con todas sus filas. Lo detectamos: eran **mil doscientos veinticuatro pozos** que se colaban. Ahora el universo se define **solo con train**. El **leakage de vocabulario**, en el encoding de categóricas. Y el **leakage entre folds** del tuning. Los cuatro están atacados y documentados por separado.

> **[Micol]:** Y antes de cualquier modelo complejo fijamos la vara: el **baseline de persistencia**, que dice "el mes que viene va a producir lo mismo que este mes". Suena tonto, pero la producción tiene una **autocorrelación de cero coma noventa y cinco** con el mes siguiente, así que es un baseline durísimo. Probamos también media móvil de tres meses y naive estacional, y los dos perdieron: la media móvil **se rezaga** al promediar, y el estacional **ignora la declinación** del pozo. La persistencia da **doscientos cincuenta coma seis de RMSE en validación**. Ese es el número a batir, y es el criterio de promoción.

---

## SLIDE 4 — Feature Store: la fuente única (cero skew)

**[VISUAL]** Captura `02_datos_feature_store_tablas_por_target.png`. Diagrama: `bronze.produccion` → **código de `ml/features.py`** → `features.feat_produccion_pozo_mensual` (+ `_gas`) → consumido **por entrenamiento y por inferencia**. Callout grande: **"misma tabla, mismo código → cero training-serving skew"**.

> **[Valentino]:** El feature store es una de las decisiones centrales de la fase. La consigna pide que las features queden persistidas y que se usen **durante la inferencia**. Nosotros fuimos un paso más allá: el store **no es una reimplementación en SQL**, es la materialización del mismo código Python que usa el entrenamiento. El asset de Dagster importa y ejecuta `ml.features.add_engineered_features` y escribe el resultado en Postgres.
>
> Evaluamos dos alternativas. **Un modelo dbt en SQL**: calzaba cuando las features eran lags simples, pero las autorregresivas por calendario y el KNN de vecinos son impracticables en SQL, y sobre todo **se desincronizarían del código de Python**, que es exactamente el problema que el store viene a evitar. El costo que pagamos es que la tabla de features queda fuera del linaje dbt-DataHub. Y evaluamos **Feast**, el feature store dedicado: es el estándar de industria, pero agrega un servicio y un modelo conceptual nuevos que hay que operar, y su online store con Redis no aporta nada porque **no tenemos serving live**. Exceso de ingeniería para nuestro volumen. Es el mismo criterio con el que descartamos Cube.dev en la Fase 2.

> **[Valentino]:** El problema que esto resuelve se llama **training-serving skew**: que el modelo se entrene con una versión de las features y se sirva con otra ligeramente distinta, y que las predicciones se degraden **en silencio**, sin que ningún test falle. Como se ve en la captura, tenemos **una tabla por target** —veintisiete features para petróleo, diecinueve para gas— y entrenamiento e inferencia leen exactamente esa misma tabla. El skew queda descartado **por construcción, no por disciplina**.
>
> También decidimos el layout. Descartamos una **tabla ancha** con ambos targets, porque mezcla dos universos de pozos distintos y deja filas con target nulo; y descartamos una **columna `target` con particiones**, porque duplica todas las filas y obliga a filtrar en cada lectura. Una tabla por target, y agregar un target es agregarlo a una constante.

> **[Micol]:** Y validamos esa paridad, no la asumimos: comparamos columna por columna las features que salen del store contra las que arma el pipeline de entrenamiento, para los dos targets. **Cero diferencias.**

---

## SLIDE 5 — Feature engineering y preprocesamiento

**[VISUAL]** Dos columnas.
Izquierda: tabla de features derivadas (roll3, delta1, lag12, acum6, water_cut, produjo_mes_pasado, prod_vecinos_mean) con la columna "qué captura".
Derecha: tabla de decisiones de preprocesamiento con el RMSE de cada opción:

| Preprocesamiento de volúmenes | ridge val RMSE |
|---|---|
| `log1p` | 496,6 |
| clip percentil 1–99 | 277,5 |
| clip solo en tef/delta | 245,3 |
| **sin clip, solo imputación** | **242,8** ← |

> **[Micol]:** Las features derivadas capturan cuatro cosas: **nivel** reciente con medias móviles, **tendencia** con deltas de declinación, **estacionalidad** con lags de doce meses, y **agotamiento** con acumulados y con el water cut, que es el ratio de agua sobre líquido total.
>
> Dos detalles de implementación que valen la pena. Primero: los lags **no se calculan con un `shift` de filas**, se calculan con un **merge por calendario**. Si un pozo tiene un hueco en su serie mensual, el `shift` te trae la fila anterior *disponible*, que puede no ser el mes que buscabas, y el lag queda con el horizonte equivocado, **contaminado en silencio**. Con el merge, si el mes no existe, queda nulo, que es honesto. Segundo: **ninguna feature aprende parámetros**. Todas son funciones puras de la serie del propio pozo hasta el mes t. El escalado y la imputación **no viven en la feature**: viven en el `Pipeline` de sklearn. Así el leakage se controla en un solo lugar.

> **[Micol]:** Y sobre el preprocesamiento tomamos una decisión contraintuitiva que la tabla justifica. Lo natural con un target de cola pesada sería aplicarle **logaritmo** o **recortar los outliers**. Probamos las dos: el `log1p` **empeora el RMSE a cuatrocientos noventa y seis**, y el clip a doscientos setenta y siete. ¿Por qué? Porque la métrica es RMSE, y en RMSE **los pozos grandes dominan el error, y su producción extrema es exactamente la señal** que necesitás para predecir su producción futura. Al recortarla, el modelo deja de distinguirlos. Los extremos son señal, no ruido. Así que no transformamos ni recortamos nada.
>
> Los nulos sí los imputamos, pero **por tipo de feature, no uniformemente**. Un `lag12` que no existe porque el pozo es nuevo **no es "la mediana de producción"**, es "no había historia". Entonces: volúmenes y lags van a **cero más un flag** que le dice al modelo que era faltante; físicas estáticas van a la **mediana de train**; categóricas van a una categoría **`DESCONOCIDO`**. Eso hace que el arranque en frío de un pozo sea **inspeccionable** en vez de una mentira.

> **[Tomás]:** Y ese `DESCONOCIDO` no es defensivo, es necesario. En el conjunto de test, el **diez coma cuatro por ciento de las filas tiene una empresa operadora que no existe en train**: trece operadoras nuevas. Eso no es un error de dato, es **cambio de distribución real** en la industria. El encoder se ajusta solo con train, y todo lo no visto cae en `DESCONOCIDO`. Es el puente entre reentrenos: cuando esa empresa ya tenga historia, el próximo retrain le da su columna propia.

---

## SLIDE 6 — Selección de features y forecast recursivo

**[VISUAL]** Arriba: barras horizontales de permutation importance en petróleo, mostrando que `prod_pet` (461,68 m³) pesa **~4× más** que la siguiente (`roll3`, 113,39), y la línea de corte en importancia = 0 → **27 features en petróleo / 19 en gas**.
Abajo: esquema del forecast recursivo (t+1 → realimenta → t+2 → … → 12 meses). Callout: **"features recursion-safe: solo las que se pueden recalcular hacia el futuro"**.

> **[Micol]:** Dos decisiones de modelado que están conectadas, y el orden importa: la segunda condiciona la primera.
>
> Empiezo por la segunda. Para pronosticar doce meses hacia adelante **no alcanza con un modelo que predice un mes**. Lo hacemos de forma **recursiva**: predecimos el mes que viene, realimentamos esa predicción como si fuera un dato real, recalculamos las features sobre la serie extendida, y predecimos el siguiente. Acá apareció el hallazgo que nos reordenó el diseño: **una feature solo puede entrar al modelo si se puede recalcular hacia el futuro**.

> **[Micol]:** Eso parte las features en tres grupos. Las **autorregresivas** —medias móviles, lags, acumulados del propio target— se recalculan en cada paso a partir de la trayectoria predicha. Las **estáticas** —profundidad, coordenadas, área de yacimiento— nunca cambian: se leen del store y se replican. Y hay un tercer grupo que **tuvimos que sacar del modelo**: `water_cut` necesita la producción de agua del mes que viene, que no predecimos. `prod_vecinos_mean` necesitaría predecir a todos los pozos vecinos. Esas no son *recursion-safe*, y quedaron fuera aunque estaban en el diseño original de features.
>
> Hay un detalle fino que vale la pena: el **mes calendario** no es ni estática ni autorregresiva. Se conoce de antemano para cualquier mes futuro. Así que en cada paso lo sobreescribimos con el **mes objetivo**, no con el del mes base.

> **[Micol]:** Con ese candidato de treinta y cinco features recursion-safe, hicimos la selección. Medimos **permutation importance sobre validación** —cuánto empeora el RMSE si rompemos esa feature— y cortamos en **ganancia positiva**: nos quedamos con toda feature que reduce el error. Es un corte **empírico, sin umbral arbitrario**. Da veintisiete en petróleo y diecinueve en gas.
>
> Descartamos dos criterios alternativos: un **top-k puro** habría dejado solo autorregresivas, y entonces un pozo nuevo —sin historia— se quedaba **sin ninguna señal**; y un **corte por parsimonia** a diez o catorce features cedía un tres por ciento de RMSE por un umbral que elegíamos a dedo. La ganancia positiva conserva solas las anclas estáticas que sostienen el arranque en frío.
>
> Y un hallazgo: los sets de petróleo y gas **no son el mismo set con otro prefijo**. En gas, las anclas geográficas —área, profundidad, coordenadas— tienen importancia **negativa** y no entran. El gas se apoya casi solo en categóricas. La asimetría está documentada.

---

## SLIDE 7 — Tracking y reproducibilidad: MLflow

**[VISUAL]** Captura `08_mlflow_experiment_runs_metricas.png` (runs con métricas) y `09_mlflow_run_params_metricas.png` (params + métricas de un run). Al costado, tabla de la comparación de modelos.

> **[Tomás]:** Para el tracking elegimos **MLflow, self-hosteado**. La consigna pide una plataforma que haga el entrenamiento **reproducible**.
>
> Las alternativas eran claras. **Weights & Biases, Neptune, Comet**: tienen UIs mucho más pulidas, pero son SaaS de pago y los datos de experimentos **salen de nuestra infraestructura** hacia la nube del proveedor. Es exactamente el mismo argumento con el que en la Fase 1 elegimos Prometheus y Grafana sobre DataDog y New Relic: consistencia de criterio a través de fases. Y la otra alternativa, **loguear a mano en un CSV o una tabla propia**: control total, cero dependencias, pero implica reinventar el registro de runs, la comparación, el almacenamiento de artefactos y —sobre todo— **el model registry con estados**, que es justo lo que necesitamos para promover. Mucho costo de desarrollo para reconstruir algo maduro. Lo que pagamos por elegir MLflow es **operarlo nosotros**, y una UI más básica.

> **[Tomás]:** En la captura se ve qué queda registrado en cada corrida: los **hiperparámetros**, las **métricas de dev y de test**, el **Pipeline serializado completo** —no solo el modelo, sino el preprocesamiento— y una **versión de los datos**, que es un hash del archivo fuente más las fechas de corte más el tamaño de cada split. Con eso cualquier run es reproducible: sabemos exactamente con qué datos y con qué configuración se entrenó, y podemos distinguir un run normal de uno de backfill.

> **[Micol]:** Y acá está el resultado que la tabla cuenta mejor que nosotros. Comparamos tres familias: **Ridge**, **Random Forest** y **XGBoost**, todas contra la persistencia. **Sin tunear, XGBoost ganaba** —doscientos treinta y ocho de RMSE— y **Random Forest quedaba por debajo del baseline**: doscientos cincuenta y uno contra doscientos cincuenta coma seis. Con tantas dummies ralas del one-hot, el Random Forest por defecto **promedia de más**.
>
> Pero al tunear con búsqueda aleatoria, **se dio vuelta**: Random Forest bajó a **doscientos veintisiete** y pasó a ser el campeón en los dos targets. Por eso el ADR-034 propone XGBoost y el ADR-039 lo revisa a Random Forest, con la evidencia adelante. La decisión no es una preferencia, es una tabla.
>
> Un punto sobre las métricas: reportamos **RMSE y R²** juntos, y no es redundante. El RMSE es la métrica de selección porque **penaliza los errores grandes**, que son los pozos que importan. Pero el RMSE es absoluto: en test baja simplemente porque el período tiene producciones de menor magnitud. El **R² es el comparable entre períodos**. Y al revés: mirando solo R², todos los modelos parecen buenos —todos rondan cero coma ochenta y ocho— porque la autocorrelación ya explica casi toda la varianza. Ninguna de las dos métricas alcanza sola. Descartamos MAPE porque **el veinticinco por ciento de los targets es cero** y el denominador explota.

> **[Valentino]:** Del lado de la infraestructura, el servidor de MLflow lo levantamos con **backend en Postgres**, en una base separada, no en SQLite. La razón es concreta: **SQLite no soporta escritura concurrente**, y si más de un proceso escribe el archivo directamente, se corrompe. Además el file-lock falla en volúmenes de red. Y descartamos un MLflow **gestionado** —Databricks, SageMaker— por costo y por el mismo criterio de self-hosted. Los artefactos van a un volumen Docker y MLflow **los sirve por HTTP**: la API descarga el modelo por red, sin montar volúmenes compartidos. Esa decisión es la que después nos permitió correr todo en local sin tocar código.

---

## SLIDE 8 — La puerta de promoción automática (el hallazgo)

**[VISUAL]** Capturas `11_mlflow_v1_production.png` y `12_mlflow_v2_staging_puerta_promocion.png` lado a lado. Entre las dos, una flecha tachada y el texto: **"el retrain generó v2, pero NO la promovió: no supera al campeón vigente"**.

> **[Tomás]:** Este es, para nosotros, el hallazgo más interesante de la fase, y se ve directo en estas dos capturas. El model registry marca la **versión uno como Production** y la **versión dos —la que produjo el reentrenamiento— como Staging**. ¿Por qué no la promovió?
>
> Porque la promoción es **automática pero condicional**. El criterio, que está implementado como una función pura y testeada en `ml/registry.py`, tiene dos condiciones. Primera: el candidato tiene que **superar al baseline de persistencia**. Segunda: tiene que **mejorar al modelo que hoy está en Production** —o bien no haber ninguno, y entonces es el primer campeón y entra. Si no cumple, la versión **queda en Staging** para inspección manual: no se descarta, pero no toca producción.
>
> En la captura, la versión dos se entrenó sobre los mismos datos, así que da prácticamente el mismo RMSE. No mejora nada. El sistema la deja en Staging. Y cuando sí promueve, el Production anterior **se archiva automáticamente**: nunca hay dos modelos en Production.

> **[Micol]:** El punto conceptual es que **no todo reentrenamiento pisa producción a ciegas**. La automatización entrena y registra **siempre** —eso es lo que te da el historial— pero la puerta de promoción **protege el serving**. Si el modelo nuevo es peor o igual, producción se queda con el que ya estaba.
>
> Los números de la vara: en petróleo el modelo da **ciento cincuenta y siete coma cinco** de RMSE en test contra **ciento sesenta y seis coma dos** de la persistencia, un cinco por ciento de margen. En gas, **cuatrocientos nueve coma dos** contra **cuatrocientos cincuenta y siete coma ocho**, once por ciento. Los dos pasan, en validación y en test. Y notemos que el margen **se achica en test** respecto de validación: fuera del período donde elegimos el modelo, el *edge* sobre una regla trivial es más chico. Es honesto decirlo.

---

## SLIDE 9 — Reentrenamiento orquestado y reproceso por fecha

**[VISUAL]** Captura `04_dagster_lineage_retrain_automation.png` (el grafo `features_refrescadas → modelo_reentrenado → forecast_precomputado`, particionado por día, con Schedule y Sensor). Abajo, `06_dagster_runs_historial.png` y `07_dagster_retrain_run_exitoso.png`. Referencia al video `05_dagster_retrain_trigger.mp4`.

> **[Valentino]:** El reentrenamiento **no es un script suelto**: es un job de Dagster, el mismo orquestador de la Fase 2. Como se ve en el grafo, encadena tres assets. Primero **refresca el feature store** desde Bronze, reescribiendo las dos tablas. Después **reentrena los dos modelos**, corriendo el comando de training una vez por target, cada uno con su experimento y su modelo de registry. Y por último **precalcula el pronóstico** de doce meses de cada pozo.
>
> Es **recurrente y automático por dos vías**: un **Schedule mensual** que corre el día seis —después del refresh del data warehouse del día cinco— y un **Sensor** que dispara cuando detecta que llegaron datos nuevos al store. Los dos requieren el dagster-daemon corriendo. Descartamos dos alternativas: un **cron del sistema operativo** habría sido más liviano, pero entonces el schedule vive en el crontab y no en Dagster, y no hay sensor natural por datos; y **solo manual** directamente no cumple el requisito.

> **[Valentino]:** Ahora el punto que la consigna pide explícitamente: **repetir el entrenamiento para un día dado**. El job está **particionado por día**, así que reentrenar "como si fuera el seis de abril" es materializar esa partición. Descartamos pasar la fecha como un parámetro de configuración ad-hoc, porque eso **no te deja el historial particionado ni el soporte de backfill** que Dagster ya te da gratis.
>
> Y hay un detalle que no es cosmético. La clave de partición se inyecta como la variable de entorno **`RETRAIN_ASOF`**, y el pipeline de entrenamiento la honra: **recorta el dataset a los períodos menores o iguales a esa fecha, antes de definir el universo de pozos y antes de calcular las features**. O sea que un reproceso de una fecha pasada **no usa datos posteriores a esa fecha**. Es el mismo principio anti-leakage del split, aplicado al backfill. Sin eso, "reentrenar como si fuera abril" sería una mentira: estarías entrenando con datos de mayo.

> **[Valentino]:** En la demo mostramos el trigger en vivo: con un clic lanzamos el retrain para una partición, y en el historial se ve la corrida completándose con éxito. Regenera el store, entrena los dos targets, y deja el precómputo con **treinta y seis mil doscientas dieciséis filas de petróleo y cuarenta y un mil trescientas dieciséis de gas** — que son exactamente doce meses por cada pozo del universo de cada target.

> **[Tomás]:** Un detalle de diseño del tercer paso. El precómputo corre **el mismo motor recursivo que usa la API**, sobre todos los pozos, con el modelo que está en Production, y guarda el resultado. Reimplementarlo aparte habría sido la forma más fácil de romper la transparencia en silencio: cualquier diferencia en el orden de los pasos o en el redondeo daría otro número. Mismo código, mismos inputs, mismos valores. Eso habilita lo que viene.

---

## SLIDE 10 — Serving: la API `/forecast`

**[VISUAL]** Capturas `13_api_swagger_contrato.png` y `14_api_forecast_curl_respuestas.png`. Diagrama de decisión: `request → ¿hay precómputo fresco? → sí: lookup (sin MLflow) / no: motor recursivo on-the-fly`.

> **[Tomás]:** Del lado del consumo, la API expone `/forecast`. Mantiene **el mismo contrato de la Fase 1** —mismo endpoint, misma autenticación por API key, misma forma de respuesta— pero ahora devuelve un pronóstico real del modelo en vez del mock de declinación lineal. Lo único que agregamos es un parámetro **opcional** `target`, que elige petróleo o gas. Quien no lo pasa, recibe petróleo, igual que antes: **un cliente de la Fase 1 no se entera de que existe**.
>
> Y quiero remarcar algo sobre ese parámetro: está declarado como un tipo cerrado de dos valores. Esa única línea es simultáneamente **la validación, la documentación de Swagger y el contrato publicado**. No se pueden desincronizar. Si mandás un target inválido, la API responde cuatrocientos veintidós sin que hayamos escrito un solo `if`.
>
> En la captura se ve sirviendo petróleo y gas, y el manejo de errores: **cuatrocientos cuatro** si el pozo no existe en el warehouse o no tiene historia en el feature store, **cuatrocientos veintidós** si el rango de fechas es inválido o no tiene meses futuros, **cuatrocientos tres** si falta la API key, y **quinientos tres** si el modelo no está disponible.

> **[Tomás]:** Y acá está el beneficio del precómputo. La API **primero busca el pronóstico precalculado**; si está fresco, responde con un lookup, sin tocar el modelo ni MLflow. Solo si no hay precómputo, o si quedó viejo, cae al motor recursivo que calcula en el momento.
>
> Y "fresco" tiene una definición precisa: el precómputo guarda **cuál era el último mes observado** cuando se generó, y la API lo compara contra el último mes que hay hoy en el store. Si no coinciden, **el dato es stale y se recalcula**. Nunca servimos un pronóstico viejo. Lo mismo si la ventana pedida no está completa en la tabla: no servimos parcial, recalculamos entero.
>
> Esto da una ventaja concreta de **resiliencia**: con precómputo fresco, **MLflow queda fuera del camino crítico del request**. Si el servidor de MLflow se cae, la API sigue respondiendo. Y es **transparente**: mismo modelo, mismas features, misma función de cálculo por los dos caminos, así que el usuario recibe siempre el mismo valor. Los dos caminos incluso comparten el helper que recorta la ventana de fechas, para que los errores sean idénticos vengas por donde vengas.

> **[Valentino]:** Sobre cómo la API se entera de que hay un modelo nuevo: evaluamos tres caminos. **Redesplegar el contenedor** al promover acopla el ciclo de ML al CI/CD de la API y deja el endpoint caído unos treinta segundos. Un **webhook desde MLflow** exige que la API sea alcanzable desde el servidor de MLflow —firewall, red— y los webhooks de MLflow tienen garantías *at-most-once*, así que igual hace falta lógica de reconciliación. Elegimos **polling en un hilo en background**: cada cinco minutos consulta el registry, y **si la versión no cambió no descarga nada**. El costo es una ventana de hasta cinco minutos entre la promoción y la disponibilidad, que para un ciclo de reentrenamiento mensual es irrelevante.
>
> Un detalle de concurrencia del que estamos orgullosos: el forecast recursivo hace **doce predicciones encadenadas**. Si el poller cambiara el modelo en el medio, los primeros meses saldrían de un modelo y los últimos de otro. Por eso el motor **toma un snapshot de la referencia al modelo al empezar** y usa esa misma durante toda la recursión.

---

## SLIDE 11 — CI/CD y despliegue

**[VISUAL]** Diagrama del pipeline de GitHub Actions:

```
push / PR (staging | main)
   ├── test           → ruff + pytest api/tests
   ├── test-pipeline  → pytest data_pipeline/tests
   └── test-ml        → pytest forecast + feature store + precompute + registry
                                   │
                                   ▼  (needs: los 3)
                                build → docker build → Trivy scan → smoke test /health
                                        → push a ECR (OIDC, sin claves estáticas)
                                   │
                                   ▼
                     deploy (main) / deploy_dev (staging)
                        → AWS SSM (sin abrir SSH)
                        → git reset --hard  +  docker compose up -d
                        → health check ×6  →  ROLLBACK automático si falla
```

> **[Tomás]:** La consigna pide que **los pipelines de procesamiento se desplieguen mediante un pipeline de CI/CD**. Nuestro CI tiene cinco jobs y una **compuerta de calidad** explícita.
>
> Tres jobs de test corren en paralelo: uno para la API, **uno para el pipeline de datos** —toda la suite de `data_pipeline`— y **uno para ML**, que cubre el motor de forecast recursivo, la construcción del feature store, el precómputo y el criterio de promoción del registry. El job de `build` **depende de los tres**: si cualquier suite falla, no hay build ni deploy. O sea que **ningún cambio al código de los pipelines llega a producción sin que sus tests pasen**.
>
> El `build` construye la imagen, la escanea con **Trivy** buscando vulnerabilidades críticas y altas, la levanta y le pega a `/health` como smoke test, y recién ahí la sube a ECR. La autenticación contra AWS es por **OIDC**: no hay claves estáticas guardadas en GitHub.

> **[Tomás]:** Y el deploy corre por **AWS Systems Manager**, no por SSH: nunca abrimos el puerto veintidós. El comando remoto sincroniza el repositorio en la instancia, levanta los contenedores, y hace hasta **seis reintentos de health check**. Si el servicio no responde, **hace rollback automático a la imagen anterior**, cuyo digest guardó antes de actualizar. El job además poletea el resultado del comando remoto, así que un deploy fallido **falla el workflow** en vez de quedar como fire-and-forget.
>
> Sobre el código de los pipelines de procesamiento: se despliega en ese mismo paso, porque la sincronización del repositorio trae los assets de Dagster, los modelos de dbt y el código de `ml/`, y el daemon ejecuta ese código recién sincronizado. Para ser precisos: **el runtime de Dagster y el de MLflow están detrás de perfiles de Docker Compose y se activan on-demand**, así que el CD despliega el código y actualiza la API, y los servicios de orquestación se levantan por perfil. Es una decisión de costo: mantener el daemon veinticuatro por siete en la instancia consumía RAM que no necesitábamos para una entrega sin servicio live.

---

## SLIDE 12 — Correr todo en local

**[VISUAL]** Los perfiles de `docker compose` y los puertos:

| Servicio | Perfil | Puerto |
|---|---|---|
| API | *(default)* | 8000 |
| MLflow | `ml` | 5000 |
| Postgres (DW) | `local-db` | 5432 |
| Dagster | `orchestration` | 3070 |
| Grafana | *(default)* | 3000 |

Callout: **"mismos contratos, sin AWS"**.

> **[Valentino]:** Como dijimos al inicio, esta entrega corre enteramente en local. Levantamos el Postgres del data warehouse, el servidor de MLflow y la API con los perfiles del compose, y el pipeline de datos y el retrain con el servicio de Dagster. Reconstruimos el **medallion completo** en el Postgres local, materializamos el feature store, entrenamos los dos modelos y los servimos. Todo en la máquina, sin instancias de AWS.
>
> Los perfiles no son un detalle: son lo que hace que un `docker compose up` pelado levante solo lo que la API necesita, y que el orquestador, el BI y MLflow se activen cuando los querés. En producción, ese mismo mecanismo es el que decide qué corre en la instancia.

> **[Tomás]:** Y la decisión de arquitectura que hizo esto portable fue que **MLflow sirve los artefactos por HTTP** —con el flag `--serve-artifacts`— y que el training y la API leen todo por **variables de entorno**. Sin eso, la API habría necesitado montar el mismo volumen de disco donde el training deja los modelos, y eso te ata las dos cosas a la misma máquina.
>
> El mismo código que corría contra el RDS y las EC2 corre contra los contenedores locales **cambiando solo la configuración**. No cambiamos una línea de lógica, solo el entorno. Eso es, en el fondo, la prueba de que los contratos entre piezas estaban bien puestos.

---

## SLIDE 13 — Demo

**[VISUAL]** Slide de transición, fondo oscuro: "Demo Fase 3" + tres íconos con labels: "Dagster — trigger de retrain" · "MLflow — runs y registry" · "API — `/forecast`".

> **[Micol]:** Pasamos al recorrido de la demo. Está documentado con capturas en el repositorio, en `docs/demo_adenda3/`.

**Orden y qué decir en cada paso** (~3 min):

1. **Datos** (capturas 01–02, ~20 s) — el medallion en el Postgres local: Bronze cuatrocientos diez mil novecientas cuarenta y cinco filas, Silver cuatrocientas diez mil novecientas cuarenta y dos. **Las tres filas de diferencia son medidas negativas que la capa de calidad mandó a cuarentena** — no se descartan en silencio, quedan auditables. Y el feature store, con una tabla por target.

2. **Dagster** (capturas 03–07 + **video 05**, ~60 s) — el grafo de assets del medallion, el job de retrain con sus particiones, **el trigger en vivo del retrain para una fecha dada**, y el run completándose con éxito. *Este es el punto que la consigna pide explícitamente demostrar.*

3. **MLflow** (capturas 08–12, ~60 s) — los runs con sus métricas (**"métricas de training en distintos runs"**, textual de la consigna), los params de un run, el registry con los dos modelos, y —el momento clave— **la versión uno en Production y la dos en Staging**: la puerta de promoción funcionando.

4. **API** (capturas 13–14, ~40 s) — el contrato en Swagger y las respuestas reales de `/forecast` para petróleo, para gas, y **los casos de error**: pozo inexistente, rango inválido, sin API key. *"Ejemplos de llamadas a la API dando predicciones en distintas condiciones"*, también textual de la consigna.

---

## SLIDE 14 — Cierre

**[VISUAL]** Bullets de logros. Frase de cierre + "Preguntas".

- Feature store como **fuente única** — cero training-serving skew, paridad validada
- **Tracking reproducible** + registry por target (MLflow)
- Retrain **orquestado, recurrente y automático**, con reproceso por fecha anti-leakage
- **Promoción automática con criterio** — la puerta protege producción
- API con **forecast recursivo + precómputo** — resiliente a la caída de MLflow
- **CI/CD** con compuerta de tests y rollback automático
- Todo en local con Docker · **cada decisión documentada en un ADR con alternativas**

> **[Tomás]:** En resumen: un sistema de machine learning de punta a punta. El feature store da una fuente única de features que elimina el skew. MLflow hace el entrenamiento reproducible y versiona los modelos con un criterio de promoción que protege producción. El reentrenamiento está orquestado, es automático y se puede reproducir para cualquier fecha del pasado sin usar el futuro. La API sirve el pronóstico de forma resiliente. Y el CI/CD no deja pasar un cambio a los pipelines sin sus tests.

> **[Micol]:** Y esto cierra el arco del proyecto. De una **API con datos simulados** en la Fase 1, a una **plataforma de datos gobernada** en la Fase 2, a un **sistema de machine learning reproducible** en la Fase 3. Todas las decisiones que contamos están en los ADRs, cada una con las alternativas que evaluamos y por qué las descartamos. Gracias, quedamos para preguntas.

---

# Anexo A — Referencia de slides para diseño

| # | Slide | Visual sugerido |
|---|---|---|
| 1 | Portada | Título, equipo, logos del stack |
| 2 | Arquitectura Fase 3 | Diagrama store → training → MLflow → API + retrain |
| 3 | Problema de modelado | Línea de tiempo del split + las 4 formas de leakage |
| 4 | Feature store | Captura 02 + diagrama "misma tabla, cero skew" |
| 5 | Features + preprocesamiento | Tabla de features + tabla de RMSE por preprocesamiento |
| 6 | Selección + recursivo | Barras de importancia + esquema del forecast recursivo |
| 7 | MLflow tracking | Capturas 08/09 + tabla comparativa de modelos |
| 8 | Puerta de promoción | Capturas 11 (Production) y 12 (Staging) |
| 9 | Retrain (Dagster) | Captura 04/06/07 + video 05 |
| 10 | Serving `/forecast` | Capturas 13/14 + diagrama lookup/fallback |
| 11 | CI/CD | Diagrama del pipeline de GitHub Actions |
| 12 | Correr en local | Perfiles docker + puertos |
| 13 | Demo | Transición con los tres íconos |
| 14 | Cierre | Bullets de logros + "Preguntas" |

---

# Anexo B — Hallazgos clave para Q&A

Si preguntan por las decisiones más importantes de la fase:

1. **El leakage tiene cuatro caras, no una.** Temporal (split por fecha), de selección (universo definido solo con train → excluye 1.224 pozos), de vocabulario (el encoder se ajusta solo con train), y entre folds (el `Pipeline` se reajusta con el train de cada fold del tuning).

2. **Feature store = código de `ml/` materializado**, no SQL reimplementado → cero training-serving skew. Paridad validada: **0 diferencias**.

3. **Forecast recursivo** → para pronosticar varios meses hay que realimentar la predicción, y eso **obliga a features recursion-safe**. Sacó `water_cut` y `prod_vecinos_mean` del modelo.

4. **Selección por ganancia positiva** (permutation importance > 0) → 27 features en petróleo, 19 en gas. Corte empírico, no un top-k arbitrario, para no dejar al cold-start sin señal.

5. **Los extremos son señal, no ruido.** `log1p` y clip **empeoran** el RMSE (496,6 y 277,5 vs 242,8), porque en RMSE los pozos grandes dominan el error y su producción extrema es lo que predice su producción futura.

6. **Random Forest ganó por tuning, no por default.** Sin tunear quedaba *por debajo* del baseline (251,9 vs 250,6). XGBoost era el mejor sin tunear. La decisión es una tabla, no una preferencia.

7. **MLflow con backend Postgres** → tracking reproducible y registry persistente. SQLite se corrompe con escritura concurrente.

8. **Promoción automática con criterio** → la v2 del retrain quedó en Staging por no superar al campeón. La puerta protege producción, y al promover el Production anterior se archiva solo.

9. **Precómputo del forecast** → saca a MLflow del camino crítico del request. Si MLflow cae, la API sigue sirviendo. Frescura verificada por dato (último mes observado), no por versión de modelo.

10. **Serving transparente** → lookup precomputado con fallback al motor recursivo; **mismo código, mismos valores** por ambos caminos.

11. **`RETRAIN_ASOF`** → el reproceso "como si fuera el día X" recorta el dataset a `periodo <= X` **antes** de definir universo y features. Sin eso, el backfill sería leakage.

12. **`snapshot_model()`** → el forecast recursivo toma la referencia al modelo una sola vez, para que una recarga en background no intercambie el modelo a mitad de las 12 predicciones encadenadas.

13. **Todo en local con Docker** → artefactos de MLflow por HTTP + configuración por variables de entorno. El mismo código corre local o en AWS.

## Preguntas incómodas que podrían hacer (y qué contestar)

**"¿El `log1p` lo descartaron o es mejora futura? Sus ADRs dicen las dos cosas."**
Real. El ADR-034 lo lista como mejora futura ("se evaluará tras fijar el algoritmo") y el ADR-038 lo evaluó y lo descartó con evidencia (ridge val RMSE 496,6 vs 242,8). **La versión correcta es la del 038: descartado con número.** El 034 es anterior y quedó desactualizado. Conviene decirlo así, no negarlo.

**"¿La promoción usa val o test?"**
El ADR-039 dice "supera la persistencia en val y lo confirma en test". La implementación de `promotion_decision()` evalúa **ambas condiciones sobre test RMSE**. Es una diferencia real entre el ADR y el código. Si lo preguntan, responder por el **código**, que es lo que la demo muestra, y reconocer que la redacción del ADR es más laxa que la implementación.

**"¿El CD despliega Dagster?"**
El CD despliega **el código** de los pipelines (sincroniza el repo en la instancia) y está **gateado por los tests de `data_pipeline` y de `ml/`**. El **runtime** de Dagster y el de MLflow están detrás de perfiles de Compose y se activan on-demand, por costo de RAM. No afirmar que el CD levanta el container de Dagster, porque no lo hace.

**"¿Qué pasa si un pozo es totalmente nuevo?"**
No es predecible hoy: no tiene fila en el feature store porque el universo se define con train. Es una limitación **de serving**, no de selección de features, y está documentada. El "cold-start" que sí manejamos es un pozo *dentro* del universo, al inicio de su serie: ahí las autorregresivas van a 0 + flag y la predicción se apoya en las estáticas.

**"¿Por qué el horizonte es de 12 meses?"**
Porque el forecast es recursivo: cada paso se apoya en la predicción anterior, así que **el error se acumula**. Doce meses acota esa acumulación y cubre un ciclo estacional. Si el rango pedido lo supera, se recorta y se devuelven los meses hasta el tope.

**"¿Por qué RMSE y no MAE?"**
Porque el target tiene **cola pesada** y los pozos de mayor producción concentran el error — y son la señal a captar. El MAE no los penaliza. Reportamos MAE igual, como referencia interpretable. MAPE queda descartada porque el 25% de los targets es cero.

---

# Anexo C — Números verificados (usar estos, no los del README de la demo)

| Concepto | Valor | Fuente |
|---|---|---|
| Pozos totales / universo petrolero final | 4.929 / **3.018** | ADR-028, ADR-031 |
| Pozos excluidos por universo train-only | **1.224** | ADR-031 |
| Filas del dataset | **319.554** | ADR-031 |
| Split (train / val / test) | 223.615 / 47.883 / 48.056 | ADR-031 |
| Cortes del split | train ≤ 2023-07 · val ≤ 2024-11 · test → 2026-04 | ADR-028 |
| Targets en cero | **~25%** | ADR-031 |
| Columnas one-hot | **365** (28 → 379 columnas) | ADR-032 |
| Filas de test con `empresa` no vista en train | **10,4%** (13 operadoras) | ADR-032 |
| Features seleccionadas | **27** petróleo / **19** gas | ADR-041 |
| Importancia de `prod_pet` vs siguiente | 461,68 vs 113,39 m³ (**~4×**) | ADR-041 |
| **Persistencia — petróleo** | val RMSE **250,6** / R² 0,881 · test **166,2** / 0,854 | ADR-029, ADR-039 |
| **Campeón (RF) — petróleo** | val RMSE **227,5** / R² 0,902 · test **157,5** / **0,869** | ADR-039, ADR-041 |
| **Persistencia — gas** | val RMSE **635,5** / R² 0,831 · test **457,8** / 0,813 | ADR-039 |
| **Campeón (RF) — gas** | val RMSE **576,6** / R² 0,861 · test **409,2** / **0,850** | ADR-039, ADR-041 |
| RF sin tunear (val) | **251,9** — *por debajo* de la persistencia | ADR-034 |
| XGBoost sin tunear (val) | 238,5 — mejor sin tuning | ADR-034 |
| Hiperparámetros RF petróleo | `n_estimators=200, max_depth=24, max_features=0.5, min_samples_leaf=5` | ADR-039 |
| Hiperparámetros RF gas | `n_estimators=400, max_depth=16, max_features=0.5, min_samples_leaf=5` | ADR-039 |
| Preprocesamiento: log1p / clip / sin clip | 496,6 / 277,5 / **242,8** (ridge val RMSE) | ADR-038 |
| Filas de producción negativa descartadas | **6** (a nivel dataset ML) | ADR-038 |
| Filas a cuarentena en Silver | **3** (410.945 → 410.942) | demo |
| Precómputo | **36.216** filas petróleo / **41.316** gas (= 12 meses × pozos) | demo |
| Horizonte máximo del forecast | **12 meses** | ADR-042 |
| Polling del modelo en la API | **300 s** (5 min) | ADR-037 |
| Cron del retrain | `0 6 6 * *` (día 6, tras el DW del día 5) | ADR-040 |

> ⚠️ El `README.md` de `docs/demo_adenda3/` cita test RMSE **156,53 / 165,16** (petróleo) y **408,20 / 455,72** (gas). Son de una corrida anterior. **Los ADRs dicen 157,5 / 166,2 y 409,2 / 457,8.** Usar los de los ADRs: es lo que la cátedra va a leer. Conviene corregir el README de la demo antes de entregar.
