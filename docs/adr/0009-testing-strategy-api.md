# Título: ADR-009: Estrategia de unit testing de la API

**Estado:** Propuesto

## Contexto

La API de la plataforma de pronóstico expone una superficie con múltiples **contratos públicos** de los que dependen, hoy, varias piezas del sistema:

- **Contratos de autenticación:** los endpoints de negocio (`/wells`, `/forecast`) requieren `X-API-Key` y devuelven `403 {"detail": "Forbidden"}` cuando falla. `/health` y `/metrics` están explícitamente exentos (ADR-008) y son consumidos sin credenciales por Docker (`infra/docker-compose.yml:16`), por el pipeline de CI/CD (`.github/workflows/ci.yml`) y por Prometheus.
- **Contratos de rate limiting:** los endpoints de negocio están protegidos por `slowapi` con un límite configurable vía `RATE_LIMIT` (ADR-007), y el cuerpo del `429` viene de un handler custom definido en `app/main.py`. `/health` y `/metrics` no deben estar sujetos al límite porque hacerlo rompería el healthcheck de Docker y el scraping de Prometheus.
- **Contratos de respuesta de error:** los códigos y bodies de `403`, `404`, `422`, `429` y `500` están documentados en el Swagger de la API y referenciados por los paneles de Grafana y las reglas de Alertmanager (ADRs 003 y 004).
- **Contratos operativos:** `/health` devuelve `200 {"status": "ok"}` y es el gate del rollback automático y del `docker image prune` post-deploy (ADRs 002 y 006). `/mock-500` devuelve `500` de forma determinística para poder ejercitar el path de alertas 5xx sin inducir outages reales (ADR-008).

Una regresión silenciosa en cualquiera de estos contratos — por ejemplo, que alguien proteja `/health` con API Key "por consistencia", que el handler custom del `429` deje de devolver JSON, que `/metrics` caiga bajo rate limit, o que el detail de un `404` cambie — **no se manifiesta como un bug visible** al desarrollador que hizo el cambio, pero rompe integraciones que viven fuera del código de la API: Docker reinicia contenedores en loop, Prometheus deja de scrapear, las alertas dejan de dispararse o disparan con formato inesperado, el pipeline de deploy nunca aprueba el nuevo digest.

El repositorio ya cuenta con una suite de tests unitarios en `api/tests/` ejecutada en el job `test` de CI (`pytest api/tests/ -v`), que es prerrequisito (`needs: test`) del job `build-and-push` que publica la imagen a ECR. Sin embargo, no existe un documento que explicite **qué se testea, qué no se testea, con qué framework, y por qué** los tests forman parte del pipeline bloqueante de deploy. A falta de ese acuerdo, la suite corre el riesgo de derivar en dos direcciones opuestas e igualmente contraproducentes: por un lado, *overfitting* a la lógica interna del mock (validar fórmulas de producción, IDs específicos de pozos, estructuras internas que son de implementación y no de contrato) y por otro, *under-testing* de los contratos cross-cutting que son los que realmente sostienen la operación (rate limit, exenciones de auth, formato de errores).

El equipo necesita documentar formalmente la estrategia de testing adoptada — framework, nivel de abstracción, scope y ejecución — para que la suite se mantenga alineada con el propósito de proteger contratos observables y no se diluya en validaciones de lógica interna sin valor de regresión.
