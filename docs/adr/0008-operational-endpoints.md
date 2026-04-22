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

## Análisis de Alternativas

Se evaluaron cuatro enfoques para cubrir los dos gaps descritos:

1. **No incluir ningún endpoint operativo en la API:** mantener únicamente `/wells` y `/forecast`, y usar uno de esos endpoints como prueba de vida para Docker y CI/CD. Obliga a distribuir la API Key en scripts de deploy y pipelines (SSM, GitHub Actions), acopla el resultado del healthcheck a condiciones de negocio (formato de parámetros, disponibilidad del dato mock, rate limit) y no resuelve la validación del path de alertas 5xx.

2. **Delegar la verificación de disponibilidad a infraestructura externa:** usar `docker inspect` sobre el estado del proceso, un TCP probe al puerto 8000, o un target group de AWS ALB con health check propio. Reemplaza `/health` pero pierde la distinción entre "el proceso Python está vivo" y "FastAPI puede manejar requests HTTP", y requiere mantener configuración específica en cada entorno (docker-compose, EC2, CI) en lugar de un contrato único declarado en la API.

3. **Exponer `/mock-500` como una aplicación o servicio separado:** desplegar un container adicional dedicado sólo a generar respuestas 5xx para probar Alertmanager y Grafana. Agrega infraestructura nueva (imagen, servicio en `docker-compose.yml`, reglas de scraping en Prometheus, panel y alerta propios) y no valida realmente la instrumentación de la API de negocio, porque las métricas provendrían de otro proceso.

4. **Activar `/mock-500` sólo mediante feature flag o variable de entorno:** mantener el endpoint en el código pero condicionar su registro a una env var (`ENABLE_MOCK_500=true`) para que esté disponible en staging/dev y deshabilitado en producción. Evita exponer la ruta en prod, pero implica que el path de alertas nunca se valida contra el entorno productivo real, y agrega una bifurcación de configuración que hay que recordar mantener sincronizada entre entornos.
