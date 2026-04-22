# Título: ADR-008: Endpoints operativos y de validación (`/health`, `/mock-500`)

**Estado:** Propuesto

## Contexto

La API de la plataforma de pronóstico expone dos endpoints de negocio (`GET /api/v1/wells` y `GET /api/v1/forecast`), ambos protegidos con API Key y rate limiting (ADR-007). Restringir la superficie de la API **exclusivamente a endpoints de negocio** deja dos necesidades sin cubrir:

1. **Verificación de disponibilidad del servicio** en las capas de orquestación y CI/CD. Hoy dependen activamente de ello:
   - El `healthcheck` declarado en `infra/docker-compose.yml` (`curl -f http://localhost:8000/health`) decide si Docker marca el contenedor como `healthy` o `unhealthy` y si corresponde reiniciarlo.
   - El pipeline de CI (`.github/workflows/ci.yml`) hace `curl --fail http://localhost:8000/health` como paso obligatorio antes de publicar la imagen y ejecuta hasta 6 reintentos post-deploy en staging y producción.
   - El deploy en EC2 usa el resultado de ese check para ejecutar rollback automático al digest previo si el servicio nuevo no responde (ADR-002).
   - El `docker image prune` que libera disco en EC2 (ADR-006) corre **sólo** después de un health check exitoso para preservar la imagen anterior en caso de rollback.
   Usar un endpoint de negocio para esto obligaría a pasar la API Key dentro de los scripts de deploy, expondría los secretos en los logs de SSM/GitHub Actions, y acoplaría el resultado del healthcheck a la lógica de negocio (validación de parámetros, disponibilidad de datos mock, rate limit).

2. **Validación end-to-end del camino de alertas y dashboards de error 5xx.** Prometheus agrega respuestas 5xx en `http_requests_total` (ADR-003), Alertmanager dispara notificaciones a Slack cuando la tasa de 5xx supera el umbral (ADR-004), y Grafana pinta el panel histórico de caídas de la API. Ninguna de estas piezas se puede ejercitar sin provocar una respuesta 5xx. Las alternativas naturales — inducir un fallo real, introducir un bug artificial, o tirar abajo el contenedor — son invasivas, dejan el servicio degradado y pueden contaminar las métricas de SLO reales en producción.

El equipo necesita documentar formalmente la decisión de incluir en la API dos endpoints **fuera del dominio de negocio** (`/health` y `/mock-500`) para cubrir estos dos gaps, explicitando el propósito de cada uno y por qué se mantienen en el mismo proceso FastAPI en lugar de delegarse a infraestructura o servicios separados.
