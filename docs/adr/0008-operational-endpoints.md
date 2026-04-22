# Título: ADR-008: Endpoints operativos y de validación (`/health`, `/mock-500`)

**Estado:** Aceptado

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

3. **Activar `/mock-500` sólo mediante feature flag o variable de entorno:** mantener el endpoint en el código pero condicionar su registro a una env var (`ENABLE_MOCK_500=true`) para que esté disponible en staging/dev y deshabilitado en producción. Evita exponer la ruta en prod, pero implica que el path de alertas nunca se valida contra el entorno productivo real, y agrega una bifurcación de configuración que hay que recordar mantener sincronizada entre entornos.

## Decisión

Se decidió **incluir ambos endpoints dentro de la misma aplicación FastAPI** que sirve los endpoints de negocio, con configuraciones diferenciadas según su propósito.

### `/health`

- **Ruta pública, sin API Key, sin rate limit.** Declarada en `api/app/routes/health.py`, devuelve `{"status": "ok"}` con `200 OK`. Está explícitamente documentada como exenta de autenticación en el Swagger (`api/app/main.py`) y como exenta de rate limit en el ADR-007.
- **Desacopla la verificación de disponibilidad de la lógica de negocio.** Los scripts de deploy (`.github/workflows/ci.yml`, `infra/docker-compose.yml`) no necesitan conocer ni transportar la API Key, no dependen de la disponibilidad de los datos mock y no pueden ser bloqueados por un cambio de rate limit. El contrato del endpoint es mínimo y estable: si responde 200, el proceso FastAPI está vivo y atendiendo requests HTTP.
- **Es el gate del pipeline de despliegue y de la limpieza de disco.** El rollback automático en EC2 (ADR-002) se dispara si `/health` no responde después del nuevo deploy. El `docker image prune -af` que libera disco (ADR-006) corre **sólo** después de un health check exitoso, preservando la imagen anterior para un eventual rollback. Ambos comportamientos dependen de que este endpoint exista, sea público y sea rápido.
- **Distingue "proceso vivo" de "HTTP operativo".** Una verificación a nivel infraestructura (TCP probe, `docker inspect`) confirma que el proceso está arriba pero no que FastAPI terminó de inicializar y está respondiendo. `/health` cubre ese segundo caso sin costo adicional.

### `/mock-500`

- **Ruta pública que siempre responde `500`.** Declarada en `api/app/routes/mock_error.py`, lanza un `HTTPException(status_code=500, detail="Internal Server Error (mock)")` de forma determinística. Queda disponible en todos los entornos (dev, staging, producción) sin feature flag.
- **Habilita la validación end-to-end del path de alertas 5xx** sin inducir un outage real. Un `curl /mock-500` genera una respuesta 5xx que es instrumentada por Prometheus (ADR-003), agregada en `http_requests_total{status_code="500"}`, evaluada por las reglas de Alertmanager (ADR-004) y reflejada en los paneles de error-rate e historial de caídas de Grafana. Esto permite probar periódicamente que todo el pipeline de observabilidad sigue funcionando después de cambios en Prometheus, Alertmanager, Slack o el dashboard.
- **Queda expuesto en producción de forma consciente.** La alternativa de activarlo por feature flag (alternativa 4) fue descartada porque el mayor valor del endpoint está justamente en poder ejercitar el path de alertas contra el entorno productivo real, no contra una configuración divergente de staging.
- **El riesgo de abuso es acotado y mitigable.** Al no estar rate-limiteado, un cliente externo podría generar ruido en Slack disparando alertas falsas; el impacto se limita a ruido operativo, no a degradación del servicio ni fuga de datos. Si en el futuro el ruido se vuelve problemático, el endpoint se puede proteger con el mismo decorador `@limiter.limit(...)` que ya usan `/wells` y `/forecast`, o mover detrás de API Key, sin cambios estructurales.

## Consecuencias

**Positivas:**

- Separación clara de responsabilidades en la superficie de la API: endpoints de negocio (con API Key y rate limit), operativos (`/health`, públicos, sin rate limit) y de validación (`/mock-500`). Cada decisión de autenticación, rate limit e instrumentación queda alineada con el propósito del endpoint.
- El pipeline de despliegue es determinístico: `/health` es un signal estable, barato y libre de secretos, y soporta tanto el healthcheck de Docker (ADR-002) como el rollback automático y la limpieza de imágenes en EC2 (ADR-006).
- El path completo de alertas (Prometheus → Alertmanager → Slack → dashboards de Grafana, ADRs 003 y 004) puede ser ejercitado de forma no invasiva desde cualquier entorno, lo que permite detectar regresiones en la observabilidad antes de que un incidente real las saque a la luz.
- Los contratos de ambos endpoints están cubiertos por tests unitarios (`api/tests/test_health.py`, `api/tests/test_rate_limit.py`), incluyendo que `/health` responda 200 sin API Key y que ninguno de los dos esté sujeto a rate limit.

**Negativas:**

- `/mock-500` queda accesible públicamente sin autenticación ni rate limit. Un actor externo podría disparar alertas falsas en Slack. El impacto es operacional (ruido), no de seguridad ni de disponibilidad. Se acepta el trade-off a la luz del tamaño del proyecto y del valor del endpoint para validar el stack de alertas; se deja abierta la opción de añadir rate limit o autenticación si el patrón de abuso justifica el cambio.
- Se suman dos rutas que hay que mantener fuera del dominio de negocio y que no aportan funcionalidad de producto. El costo de mantenimiento es bajo (ambos endpoints son triviales y no tienen dependencias de datos), pero existe.
- `/health` devuelve un payload mínimo (`{"status": "ok"}`) y no verifica dependencias externas (hoy no hay ninguna relevante, pero si en el futuro la API dependiera de una base de datos, cache o servicio externo, el endpoint podría devolver 200 con el servicio realmente degradado). Esa evolución, si ocurre, requerirá revisar este ADR y posiblemente separar `/health` (liveness) de un `/ready` (readiness) para no romper el contrato actual usado por Docker y CI/CD.
