# Título: ADR-007: Rate limiting en la API

**Estado:** Aceptado

## Contexto

La API de la plataforma de pronóstico cuenta actualmente con un único mecanismo de seguridad: una API Key estática validada en el header `X-API-Key` (`api/app/core/security.py`). Si bien la API Key impide el acceso anónimo, no protege contra los siguientes escenarios:

1. **Ataques de denegación de servicio (DoS/DDoS):** un cliente (legítimo o malicioso) puede saturar la instancia EC2 con un volumen alto de requests, degradando el servicio para el resto de los consumidores.
2. **Consumos indebidos o abusivos:** una API Key filtrada o un cliente mal implementado (loops infinitos, scripts descontrolados) puede generar tráfico desproporcionado sin que exista ningún límite que lo contenga.
3. **Protección del backend predictivo:** los endpoints `/wells` y `/forecast` realizan cómputos que, ante volumen alto de requests concurrentes, pueden comprometer la disponibilidad de la instancia (recurso crítico monitoreado en Prometheus/Grafana).

El equipo definió sumar una capa de seguridad complementaria a la API Key que mitigue estos riesgos sin introducir infraestructura adicional ni complejidad significativa en el código existente.

## Análisis de Alternativas

Se evaluaron tres enfoques para implementar el control de tasa:

1. **Rate limiting a nivel aplicación con `slowapi`:** librería nativa para FastAPI/Starlette inspirada en `Flask-Limiter`. Se configura con decoradores por endpoint y almacena el contador en memoria por defecto (opcionalmente Redis). No requiere servicios externos.
2. **Rate limiting a nivel infraestructura (AWS WAF / API Gateway):** AWS ofrece reglas de rate limiting en WAF o cuotas en API Gateway. Desplaza la responsabilidad fuera del código, pero requiere reconfigurar el ingreso de tráfico (hoy la EC2 expone FastAPI directamente) y agrega costo por request.
3. **Middleware custom en FastAPI:** implementar un contador propio usando un middleware y un diccionario en memoria. Máximo control, pero hay que resolver manualmente ventanas deslizantes, claves por IP/API Key, limpieza de entradas viejas y respuestas HTTP 429.

## Decisión

Se decidió implementar **rate limiting a nivel aplicación con `slowapi`** por los siguientes motivos:

- **Se mantiene la seguridad dentro del repositorio:** igual que el resto de decisiones del proyecto (GitOps, IaC), las reglas de límite viven versionadas junto al código y se despliegan automáticamente por CI/CD, sin configuración manual en AWS.
- **No agrega overhead significativo al código:** la integración se resuelve con un decorador por endpoint (`@limiter.limit("N/minute")`) y un handler global para respuestas `429 Too Many Requests`. No hace falta tocar la lógica de negocio.
- **Es nativo del framework:** `slowapi` está diseñado específicamente para FastAPI/Starlette y se integra con el sistema de dependencias existente. El cambio es aditivo, no invasivo.
- **Granularidad por endpoint:** `slowapi` permite aplicar límites distintos a cada ruta (o directamente no aplicar ninguno). Esto es importante porque hay endpoints que **no deben ser limitados**: concretamente `/metrics`, que Prometheus scrapea cada 15 segundos según `monitoring/prometheus.yml`. Un límite global bloquearía el scraping, rompería la recolección de métricas y apagaría los dashboards de Grafana y las alertas de Alertmanager (ADR-003 y ADR-004). Con `slowapi`, el decorador se coloca únicamente en los endpoints de negocio (`/wells`, `/forecast`), dejando `/metrics` y `/health` fuera del control de tasa por diseño.
- **Sin infraestructura adicional:** el backend por defecto es en memoria, lo cual es aceptable mientras corra una sola instancia EC2. Si en el futuro se escala horizontalmente, se puede migrar a Redis cambiando solo la configuración del `Limiter`, sin reescribir los decoradores.
- **Complementa, no reemplaza, la API Key:** la API Key sigue validando autorización (¿quién sos?), mientras que el rate limiting controla el uso (¿cuántas veces podés pedir?). Son capas ortogonales.

## Consecuencias

**Positivas:**

- Mitigación efectiva de DoS básicos y de consumos descontrolados por parte de clientes autorizados.
- Defensa en profundidad: aunque la API Key se filtre, el atacante no puede generar volumen arbitrario de requests.
- Configuración declarativa y versionada, coherente con el paradigma IaC del proyecto.
- Las respuestas `429` son automáticamente visibles en Prometheus (vía `http_requests_total` por código de estado), permitiendo alertar sobre abusos desde Grafana.

**Negativas:**

- El contador en memoria se resetea cuando el contenedor se reinicia (por ejemplo, tras un deploy). En la práctica no es un problema relevante dado el volumen actual, pero es una limitación a tener en cuenta.
- Si en el futuro se escala a múltiples réplicas de la API, el contador por instancia deja pasar hasta `N × réplicas` requests. Mitigable migrando el backend a Redis.
- Clientes legítimos que envíen ráfagas cortas pueden recibir `429`; se deberán calibrar los límites según el uso real observado en producción.
