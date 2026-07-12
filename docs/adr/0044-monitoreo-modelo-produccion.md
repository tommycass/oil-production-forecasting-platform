# Título: ADR-044: Monitoreo del modelo en producción

**Estado:** Aceptada

> Decide **cómo se detecta la degradación del modelo servido** (drift de datos, pérdida de calidad predictiva). Complementa el monitoreo de la API/infra de Fase 1 ([ADR-003](0003-prometheus-grafana-monitoreo.md)/[ADR-004](0004-alertmanager-slack-notificaciones.md)), que cubre disponibilidad, latencia y tasa de errores del servicio pero **no** la calidad de las predicciones. Cierra la delegación que dejaron abiertas [ADR-038](0038-preprocesamiento-datos.md) (outliers espurios "se delegan al monitoreo") y [ADR-032](0032-encoding-categoricas-onehot.md) (cadencia operativa ante categorías nuevas).

---

## Contexto

Un modelo en producción se degrada aunque el código no cambie: la distribución de los datos se corre (pozos nuevos, cambios operativos, rectificaciones de la fuente) y el error real crece sin que ningún test lo detecte. Hace falta un mecanismo que lo vigile.

Tres restricciones del proyecto condicionan la decisión:

1. **Los datos son mensuales y llegan con ~1 mes de latencia** (cron del DW el día 5, ADR-021): el *ground truth* de una predicción de julio recién existe a principios de agosto — y puede rectificarse después. No hay señal nueva que monitorear entre corridas mensuales.
2. **La entrega es sin servicio live 24/7 obligatorio** (adenda Fase 3): no hay tráfico real continuo cuya deriva convenga vigilar en tiempo real.
3. **Ya existe un retrain mensual automatizado** (Schedule día 6 + Sensor, ADR-040) que reentrena con la ventana corrida y **re-evalúa al Production vigente sobre el holdout más reciente** como parte del gate de promoción (ADR-039).

## Análisis de Alternativas

### Alternativa A — Plataforma de monitoreo de ML dedicada (Evidently / whylogs / NannyML)

Servicio o job que calcula data drift (PSI/KS por feature) y prediction drift contra una ventana de referencia, con dashboard propio.

**Descartada:** detecta *drift de inputs*, pero con ground truth mensual no puede medir el error real antes que el retrain (que llega un día después del cierre de datos). Agrega una pieza de infra y un dashboard nuevos a una EC2 t2.medium ya justa de RAM (mismo criterio de sobredimensionamiento con el que ADR-035 descartó Feast y ADR-027 descartó Cube.dev: sin servicio live ni datos de alta frecuencia, el costo operativo no compra información accionable que el ciclo mensual no dé igual).

### Alternativa B — Job de predicción-vs-real + métricas en Prometheus

Cuando cierra un mes, comparar el pronóstico precomputado (`features.pred_*`) contra el valor observado que acaba de entrar al store, exponer un RMSE rolling en `/metrics` y alertar por Grafana/Slack (reusa el stack de Fase 1).

**Descartada (por ahora):** es la evolución natural si el servicio pasa a ser live, pero hoy duplica con menos rigor lo que el retrain ya mide 24 horas después del cierre de datos: exige **persistir el historial de predicciones** (el precómputo actual se sobrescribe en cada corrida, ADR-043) y construir el join pred-vs-real, para obtener una señal con la misma cadencia mensual que la Alternativa C ya da gratis. Se registra como camino de upgrade, no se implementa.

### Alternativa C — Re-evaluación walk-forward del retrain como guardián (seleccionada)

Usar el ciclo mensual existente como mecanismo de monitoreo explícito:

- Cada corrida del job `retrain` (día 6, o antes si el Sensor detecta datos nuevos) reentrena un candidato **y re-evalúa al Production vigente en vivo** sobre la ventana de test más reciente (`ml/registry.py::_incumbent_test_rmse`, prediciendo con el modelo cargado del registry, con fallback al tag `test_rmse`).
- El gate de promoción (`promotion_decision`, ADR-039) actúa como detector de degradación: si el vigente empeoró frente al holdout nuevo, el candidato reentrenado lo reemplaza; si el propio candidato no supera a la **persistencia** (baseline ADR-029), no se promueve y el run queda registrado con el motivo.
- La serie histórica de `test_rmse` / `persistencia_test_rmse` por run queda en **MLflow** (un experimento por target): la evolución del error del sistema mes a mes es consultable y comparable entre versiones, que es exactamente la curva que un dashboard de drift intentaría aproximar.

**Ventajas:** cadencia alineada con la frecuencia real del dato (no hay nada que mirar entre corridas); cero infra nueva; la señal es el **error real out-of-sample**, no un proxy de drift de inputs; los casos delegados por ADR-038/032 (outliers espurios, categorías nuevas) quedan cubiertos por el mismo mecanismo — un corrimiento que degrade al modelo se refleja en el RMSE del holdout y dispara el reemplazo en la corrida siguiente.

**Desventajas:** latencia de detección de hasta un ciclo (~1 mes); no distingue *causa* (drift de datos vs. modelo envejecido — solo ve el efecto en el error); no hay alerta push: hay que mirar MLflow (la salud del *servicio* sí alerta por Slack, ADR-004).

## Decisión

**Alternativa C**: el retrain mensual con re-evaluación del incumbente y gate de promoción es el mecanismo oficial de monitoreo del modelo, con MLflow como registro histórico de la calidad. Se documenta explícitamente que:

- El monitoreo de Fase 1 (Prometheus/Grafana/Alertmanager) sigue cubriendo el **serving** (disponibilidad, latencia, 5xx de `/forecast`); este ADR cubre la **calidad del modelo**.
- Si el sistema pasara a servicio live con consumo continuo, el paso siguiente es la **Alternativa B** (persistir historial de predicciones + pred-vs-real en Prometheus), que reusa el stack existente sin cambiar esta decisión de fondo.

## Consecuencias

**Positivas:**
- Degradación del modelo acotada a un ciclo: a lo sumo un mes después del corrimiento, el gate lo detecta y el reentreno lo corrige — sin piezas nuevas de infra.
- Trazabilidad completa en MLflow: cada corrida deja `test_rmse`, `persistencia_test_rmse` y el motivo de promoción/no-promoción por target.
- ADR-038 y ADR-032 dejan de delegar a un monitoreo indefinido: la cadencia operativa es la del retrain (ADR-040).

**Negativas / trade-offs:**
- Ventana ciega de hasta un mes entre corridas; asumida porque el dato mismo es mensual.
- Sin diagnóstico de causa raíz del drift (requeriría la Alternativa A o B); ante una degradación, el análisis es manual sobre los runs de MLflow.
- La detección depende de que el daemon de Dagster esté operativo (mismo trade-off asumido en ADR-040).

## Relación con otros ADRs

- **ADR-003/004:** monitoreo del servicio (API/infra); este ADR cubre la dimensión que aquéllos no ven: la calidad predictiva.
- **ADR-029:** la persistencia es el piso que el gate exige en cada corrida.
- **ADR-039:** define el criterio de promoción que aquí se usa como detector de degradación.
- **ADR-040:** define la cadencia (Schedule + Sensor) sobre la que este monitoreo se apoya.
- **ADR-032/038:** delegaban la detección de categorías nuevas y outliers espurios "al monitoreo"; este ADR la resuelve.
- **ADR-043:** la Alternativa B (descartada por ahora) requeriría persistir el historial del precómputo que hoy se sobrescribe.
