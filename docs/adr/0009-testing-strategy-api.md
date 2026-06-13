# Título: ADR-009: Estrategia de unit testing de la API

**Estado:** Aceptado

## Contexto

La API de la plataforma de pronóstico expone una superficie con múltiples **contratos públicos** de los que dependen, hoy, varias piezas del sistema:

- **Contratos de autenticación:** los endpoints de negocio (`/wells`, `/forecast`) requieren `X-API-Key` y devuelven `403 {"detail": "Forbidden"}` cuando falla. `/health` y `/metrics` están explícitamente exentos (ADR-008) y son consumidos sin credenciales por Docker (`infra/docker-compose.yml:16`), por el pipeline de CI/CD (`.github/workflows/ci.yml`) y por Prometheus.
- **Contratos de rate limiting:** los endpoints de negocio están protegidos por `slowapi` con un límite configurable vía `RATE_LIMIT` (ADR-007), y el cuerpo del `429` viene de un handler custom definido en `app/main.py`. `/health` y `/metrics` no deben estar sujetos al límite porque hacerlo rompería el healthcheck de Docker y el scraping de Prometheus.
- **Contratos de respuesta de error:** los códigos y bodies de `403`, `404`, `422`, `429` y `500` están documentados en el Swagger de la API y referenciados por los paneles de Grafana y las reglas de Alertmanager (ADRs 003 y 004).
- **Contratos operativos:** `/health` devuelve `200 {"status": "ok"}` y es el gate del rollback automático y del `docker image prune` post-deploy (ADRs 002 y 006). `/mock-500` devuelve `500` de forma determinística para poder ejercitar el path de alertas 5xx sin inducir outages reales (ADR-008).

Una regresión silenciosa en cualquiera de estos contratos — por ejemplo, que alguien proteja `/health` con API Key "por consistencia", que el handler custom del `429` deje de devolver JSON, que `/metrics` caiga bajo rate limit, o que el detail de un `404` cambie — **no se manifiesta como un bug visible** al desarrollador que hizo el cambio, pero rompe integraciones que viven fuera del código de la API: Docker reinicia contenedores en loop, Prometheus deja de scrapear, las alertas dejan de dispararse o disparan con formato inesperado, el pipeline de deploy nunca aprueba el nuevo digest.

El repositorio ya cuenta con una suite de tests unitarios en `api/tests/` ejecutada en el job `test` de CI (`pytest api/tests/ -v`), que es prerrequisito (`needs: test`) del job `build-and-push` que publica la imagen a ECR. Sin embargo, no existe un documento que explicite **qué se testea, qué no se testea, con qué framework, y por qué** los tests forman parte del pipeline bloqueante de deploy. A falta de ese acuerdo, la suite corre el riesgo de derivar en dos direcciones opuestas e igualmente contraproducentes: por un lado, *overfitting* a la lógica interna del mock (validar fórmulas de producción, IDs específicos de pozos, estructuras internas que son de implementación y no de contrato) y por otro, *under-testing* de los contratos cross-cutting que son los que realmente sostienen la operación (rate limit, exenciones de auth, formato de errores).

El equipo necesita documentar formalmente la estrategia de testing adoptada — framework, nivel de abstracción, scope y ejecución — para que la suite se mantenga alineada con el propósito de proteger contratos observables y no se diluya en validaciones de lógica interna sin valor de regresión.

## Análisis de Alternativas

Se evaluaron cuatro enfoques para validar el comportamiento de la API:

1. **Sin tests automatizados, validación por smoke test manual post-deploy:** después de cada merge, un desarrollador corre manualmente `curl` contra los endpoints principales en staging o producción. Es el enfoque de menor costo inicial pero no escala con el ritmo de cambios del repositorio, no bloquea regresiones en el pipeline, depende de que alguien recuerde verificar cada contrato (auth, rate limit, formato de errores, exenciones operativas) y produce detecciones tardías — típicamente cuando Docker ya empezó a reiniciar el contenedor o Prometheus dejó de scrapear.

2. **Tests end-to-end contra una instancia real del stack completo:** levantar el `docker-compose.yml` con la API, Prometheus, Alertmanager y Grafana, y ejercitar los endpoints por red contra `http://localhost:8000`. Es el escenario más fiel al entorno productivo pero impone un costo alto en CI (varios minutos por corrida), introduce fragilidad por timing y red, obliga a esperar ventanas reales para testear rate limiting por tiempo (un `60/minute` requiere más de un minuto de reloj), y hace difícil aislar variables: no hay forma barata de patchear la `API_KEY` o de resetear el estado del `Limiter` entre tests sin reiniciar el stack.

3. **Tests unitarios a nivel lógica interna del mock:** testear directamente funciones como `get_forecast()` o `get_wells()` y el contenido del diccionario `WELL_BASE_PRODUCTION`, validando la fórmula de declinación lineal, los IDs de pozos mock, y las estructuras de datos internas. Da cobertura alta en el reporte pero cae en *overfitting*: la capa mock es explícitamente temporal (se reemplazará por fuentes reales de datos), por lo que estos tests se volverán obsoletos o falsamente rojos en el primer refactor serio, y mientras tanto no protegen ninguno de los contratos observables de los que depende la infraestructura (auth, rate limit, códigos y bodies de error, exenciones operativas).

4. **Tests unitarios a nivel de contrato HTTP con el `TestClient` de FastAPI:** usar `fastapi.testclient.TestClient` para ejercitar la aplicación ASGI en memoria — sin sockets, sin contenedor, sin red — y afirmar sobre el status code, headers, y body JSON devueltos. Permite patchear constantes como `app.core.security.API_KEY` con `unittest.mock.patch` para controlar el entorno por test, resetear el estado del `limiter` entre tests para cubrir rate limiting sin esperar ventanas reales, y correr la suite completa en menos de un segundo. El costo es aceptar que algunos comportamientos específicos de red (timeouts, headers insertados por proxies, comportamiento del sistema operativo bajo concurrencia) no se cubren — pero esos no son contratos que la API declare, sino propiedades del entorno de deploy.

## Decisión

Se decidió la **alternativa 4: tests unitarios a nivel de contrato HTTP con `pytest` y `fastapi.testclient.TestClient`**, con una regla explícita de scope y con ejecución bloqueante en el pipeline de CI.

### Framework y herramientas

- **Runner:** `pytest` (ya presente en `api/requirements-dev.txt`).
- **Cliente de pruebas:** `fastapi.testclient.TestClient`, que monta la app ASGI en memoria y permite hacer requests HTTP contra ella sin abrir sockets.
- **Patching:** `unittest.mock.patch` para sustituir constantes sensibles al entorno — en particular `app.core.security.API_KEY` — sin requerir variables de entorno reales ni archivos `.env` durante los tests.
- **Fixtures comunes:** un `TestClient` compartido se declara en `api/tests/conftest.py` para que todos los módulos de test usen la misma instancia de la app.

### Regla de scope

La suite testea **contratos observables desde el cliente**, no implementación interna. Concretamente:

- **Sí se testea:** códigos de estado (200, 403, 404, 422, 429, 500), bodies JSON completos (no sólo claves), headers (`content-type`), contratos de auth (header ausente/inválido/válido, case-insensitivity del nombre del header), contratos de rate limit (que efectivamente devuelve 429 al exceder el límite, que el body sale del handler custom, que `/health` y `/metrics` están exentos), exenciones de API Key en endpoints operativos (`/health`, `/metrics`), y mensajes de error descriptivos (`"Well not found"`, `"Forbidden"`, etc.).
- **No se testea:** la fórmula exacta de declinación lineal del mock (`base - day * 0.5`), los valores numéricos de `WELL_BASE_PRODUCTION`, ni la estructura interna de los servicios (`services/wells.py`, `services/forecast.py`). Estas piezas son temporales — se reemplazarán por fuentes de datos reales — y atarles tests equivaldría a bloquear refactors legítimos con fallas que no reflejan un cambio de contrato.

### Organización

La suite vive en `api/tests/` con un archivo por área de responsabilidad: `test_auth.py`, `test_health.py`, `test_metrics.py`, `test_wells.py`, `test_forecast.py` y `test_rate_limit.py`. El archivo dedicado a rate limiting usa `try`/`finally` con `limiter.reset()` alrededor de cada test para evitar contaminar el singleton compartido entre módulos de test.

### Ejecución en CI/CD

El workflow `.github/workflows/ci.yml` declara un job `test` que ejecuta `pytest api/tests/ -v` en cada push y pull request, y el job `build-and-push` declara `needs: test`. Esto convierte a la suite en un **gate obligatorio para la publicación de la imagen a ECR**: si algún test falla, la imagen no se construye, el deploy a staging o producción no ocurre, y la infraestructura productiva nunca recibe una versión que haya quebrado un contrato testeado.

## Consecuencias

**Positivas:**

- Los contratos declarados en ADRs previos quedan **anclados por tests ejecutables**: las exenciones de rate limit de `/health` y `/metrics` del ADR-007, los bodies de error, el formato del 429 del handler custom, y las decisiones operativas del ADR-008 dejan de ser promesas escritas sólo en markdown y pasan a ser verificaciones automáticas en cada push.
- Las regresiones se detectan **antes del deploy**: una ruta que accidentalmente pida API Key en `/health`, un cambio en el detail de un 404, o una regresión del handler custom del 429, son bloqueados por el gate del job `test` antes de que lleguen a ECR.
- La suite es **barata de correr**: la suite completa termina en menos de un segundo localmente, lo que mantiene el ciclo de feedback corto para quien desarrolla y hace prácticamente gratis correrla en CI en cada commit.
- El patrón de tests es **replicable**: cada vez que se agrega un contrato nuevo (un endpoint, una exención, un código de error), el costo marginal de cubrirlo es mínimo y hay precedente claro de cómo escribir el test.

**Negativas:**

- La fórmula del mock y los datos de los tres pozos no están cubiertos. Si alguien rompe la lógica de `get_forecast()` manteniendo la forma del response, los tests pasan. Se acepta el trade-off porque el mock es explícitamente temporal; cuando se reemplace por una fuente real de datos, la estrategia deberá revisarse para incluir tests de integración contra esa fuente.
- `TestClient` no ejerce la pila de red real: no cubre timeouts, comportamiento de reverse proxies ni concurrencia real entre procesos. Para el stack actual (una sola instancia EC2 detrás de Docker Compose) esto no introduce riesgo significativo, pero si en el futuro se incorpora un balanceador o múltiples réplicas, se deberá evaluar sumar un tramo de tests de integración que sí ejercite red.
- Los tests de rate limiting dependen de un detalle de implementación de `slowapi` (`limiter.reset()` para limpiar el storage). Si la librería cambia esa API, habrá que actualizar los tests afectados. El impacto está acotado a un archivo (`api/tests/test_rate_limit.py`) y compensa sobradamente el costo de testear rate limit por tiempo real.

---

## Actualización — Fase 2

**Cambios en el workflow CI:**

- El job `build-and-push` mencionado en el contexto y consecuencias fue **renombrado a `build`** al reestructurar el pipeline en Fase 2.
- Se incorporó el job **`test-pipeline`** (ejecuta los tests del data pipeline dbt + Dagster con pytest), que también es prerrequisito de `build`. La restricción original — la suite de API debe pasar antes de que se publique la imagen — se mantiene; se amplió para incluir el pipeline de datos como segundo gate paralelo.
- **Flujo actual del CI:** `test` + `test-pipeline` (paralelos) → `build` → `deploy` / `deploy_dev`.

**Alcance de los tests de la API tras el PR #81 (integración con el DW real):**

- `/forecast` dejó de validar contra el catálogo mock (`demo_data.WELLS`) y ahora llama a `well_exists_in_dw()` que consulta `gold.dim_pozo`. Los tests de la suite mockean esta función (`patch("app.routes.forecast.well_exists_in_dw")`) para no requerir una conexión real al DW en el job `test` de CI.
- La cobertura de la lógica SQL de `well_exists_in_dw` y `get_wells` es **manual**: el equipo validó el SQL contra un Postgres real antes del merge del PR #81. No existe un job de integración que ejercite el SQL contra Postgres en CI; se acepta como trade-off dado que agregar un servicio Postgres al job `test` aumentaría la complejidad del workflow y el tiempo de CI. Si en el futuro se agrega dicho job, deberá diferenciarse el scope (unit tests vs integration tests) en el workflow.
