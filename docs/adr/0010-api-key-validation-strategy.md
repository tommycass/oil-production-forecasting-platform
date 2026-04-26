# Título: ADR-010: Estrategia de validación de API Key

**Estado:** Aceptado

## Contexto

La API utiliza una API Key estática como mecanismo de autenticación: el cliente envía el header `X-API-Key` y el servidor lo compara contra la variable de entorno `API_KEY` (`app/core/security.py`). Si no coincide, se devuelve `403 Forbidden`.

La pregunta no es **si** validar la API Key, sino **dónde y cuándo** ejecutar esa validación dentro del flujo de un request en FastAPI. La elección impacta en:

1. **Filtración de información:** si la validación corre después de Pydantic, un cliente no autenticado puede recibir errores `422` que revelan la estructura interna de la API (nombres de parámetros, tipos esperados).
2. **Consumo de recursos:** parsear y validar datos cuesta CPU; hacerlo para clientes sin credenciales es desperdicio.

## Análisis de Alternativas

1. **Dependencia por endpoint (`Depends`):** patrón nativo de FastAPI. La validación se inyecta en cada ruta y se ejecuta **después** de Pydantic. Implementación original del proyecto.
2. **Middleware ASGI:** clase que extiende `BaseHTTPMiddleware` y se registra con `app.add_middleware(...)`. Intercepta cada request **antes del ruteo** y de Pydantic, valida el header y devuelve `403` si falla.
3. **API Gateway / WAF (AWS):** delegar la verificación a infraestructura externa. Saca la lógica del repositorio, agrega costo por request y obliga a reconfigurar el ingreso de tráfico.

## Decisión

Se decidió implementar la validación como **middleware ASGI** (alternativa 2), aplicando el principio de seguridad **fail-fast on auth**: rechazar lo más temprano posible a los clientes no autenticados, antes de ejecutar cualquier otra lógica.

Originalmente la validación estaba implementada como dependencia (`Depends(verify_api_key)`) en cada endpoint, lo que hacía que se ejecutara **después** de la validación de tipos de Pydantic. Esto provocaba que un cliente con API Key inválida y parámetros mal formados recibiera un `422` con detalles del schema antes de ser rechazado por falta de credenciales — exactamente lo opuesto al principio de fail-fast on auth. Se decidió moverla a middleware para que la autenticación pase a ser la primera barrera del sistema y los clientes no autorizados nunca lleguen al parser de datos.

### Justificación

- **Fail-fast on auth:** la autenticación debe ser la primera barrera del sistema, no una validación entrelazada con el parsing de datos.
- **No filtración de schema:** un cliente sin API Key válida nunca llega a Pydantic, por lo que no puede usar errores `422` para descubrir la estructura de la API.
- **Eficiencia:** el chequeo es O(1) (lectura de un header y comparación de strings), descartando requests no autorizados sin invocar parsers ni dependencias.
- **Consistencia con ADR-007:** el rate limiting también es una capa transversal. Tratar la API Key del mismo modo refuerza la separación entre seguridad e lógica de negocio.
- **La seguridad vive en el repositorio:** descarta la alternativa 3 (API Gateway), que sacaría la regla del control de versiones y agregaría costo de infraestructura sin beneficio proporcional al alcance actual.

## Consecuencias

**Positivas:**

- La API no filtra información del schema a clientes no autenticados: el primer error siempre es `403`.
- Menor costo de procesamiento de requests no autorizados.
- Endpoints más limpios: ya no declaran `Depends(verify_api_key)` en su firma.
- Código alineado con buenas prácticas de seguridad (fail-fast on auth, defensa en profundidad).

**Negativas:**

- La protección se aplica por convención de URL (prefijo `/api/`). Si se agrega una ruta protegida fuera de ese prefijo, hay que actualizar el middleware. Mitigado porque todos los endpoints de negocio actuales viven bajo `/api/v1/`.
- Se pierde la granularidad de tener distintas estrategias de autenticación por endpoint. Para el alcance actual (una sola API Key estática) no es una limitación real.
