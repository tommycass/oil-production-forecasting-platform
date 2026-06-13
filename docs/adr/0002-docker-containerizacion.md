# Título: ADR-002: Uso de Docker para contenerización
**Estado:** Aceptado

## Contexto
El equipo trabaja de forma distribuida con un modelo de ramas por funcionalidad y Pull Requests hacia main/staging. Sin automatización, la calidad del código depende de revisiones manuales propensas a error, y el despliegue a nuestro servidor AWS EC2 requiere conectarse manualmente por SSH y ejecutar scripts. Necesitamos garantizar que cada cambio no rompa el sistema, que las imágenes Docker sean seguras y publicadas, y automatizar el despliegue al entorno de desarrollo en AWS de forma continua.

## Alternativas Consideradas para el Despliegue

### Alternativa A: Despliegue vía SSH directo (GHCR + EC2 IP) - *Descartada*
El pipeline utilizaba una action de terceros (`appleboy/ssh-action`) para conectarse a la instancia EC2 utilizando una clave privada `.pem` y la IP pública del servidor, descargando la imagen desde GitHub Container Registry (GHCR).
* **Ventajas:** Fácil de implementar conceptualmente; utilizaba el registro gratuito de GitHub (GHCR).
* **Desventajas:** * **Punto de falla crítico (IP Dinámica):** Al usar una instancia EC2 básica sin IP Elástica, cada reinicio del servidor cambiaba la IP pública, obligando a actualizar manualmente el secreto `EC2_HOST` en GitHub para que el despliegue no fallara por timeout.
    * **Riesgo de seguridad:** Requería almacenar claves privadas (archivos `.pem`) de larga duración como secretos en GitHub.

### Alternativa B: Autenticación OIDC y AWS Systems Manager (ECR + SSM) - *Seleccionada*
Se establece una relación de confianza sin contraseñas (OpenID Connect) entre GitHub y AWS. GitHub asume un rol temporal de IAM para subir imágenes a un registro privado de Amazon ECR y utiliza AWS Systems Manager (SSM) para enviar el comando de actualización al agente de la instancia EC2, identificándola mediante etiquetas (tags) en lugar de direcciones IP.

## Decisión
Implementaremos la **Alternativa B**, utilizando **GitHub Actions** para orquestar un pipeline secuencial de tres etapas (`test` → `build` → `deploy`), integrado de forma nativa con **Amazon ECR** y **AWS Systems Manager**.

**Justificación de las herramientas:**
- **GitHub Actions:** Integración nativa con el repositorio, evitando la gestión de servidores CI/CD externos (como Jenkins).
- **AWS IAM OIDC:** Elimina la necesidad de almacenar credenciales de AWS de larga duración (Access Keys o `.pem`) en GitHub, mejorando drásticamente la seguridad.
- **Amazon ECR:** Almacenamiento privado de imágenes dentro de la misma red de AWS que nuestra instancia, ofreciendo descargas de contenedores más rápidas que soluciones externas.
- **AWS SSM:** Permite la ejecución de comandos remotos en la instancia sin abrir puertos SSH ni depender de IPs públicas dinámicas.

### Arquitectura del Pipeline

**1. Estrategia de Testing (`test`):**
- Se utilizan 16 tests automatizados independientes con `pytest` y `TestClient` (FastAPI) aislando responsabilidades (health, auth, wells, forecast).
- Se mockea e inyecta la API Key de desarrollo como variable de entorno de forma segura.
- Se incluye análisis estático rápido usando `ruff` sobre el directorio de producción (`api/app/`).

**2. Construcción y Registro (`build`):**
- Se construye la imagen Docker y se escanea con Trivy en modo informativo (exit-code 0 para vulnerabilidades CRITICAL/HIGH).
- Se verifica la salud post-build levantando el contenedor efímeramente y haciendo un GET a `/health`.
- GitHub Actions se autentica temporalmente en AWS asumiendo el rol `RolGitHubCI`.
- Se publican las imágenes en **Amazon ECR** con tags `latest` y el SHA del commit para permitir rollbacks.

**3. Despliegue Automatizado (`deploy` / `deploy_dev`):**
- Se ejecuta únicamente al hacer merge a `main` (producción) o `staging` (dev), apuntando a la instancia EC2 con la etiqueta correspondiente (`Name=api` o `Name=api-dev`).
- El script en EC2 implementa una estrategia de despliegue de bajo riesgo con rollback automático (ver sección siguiente).
- El job de GitHub Actions espera activamente el resultado del comando SSM (polling cada 10s, timeout 6 min) y falla si el despliegue en EC2 falla, garantizando visibilidad del estado real del servidor en el pipeline.

### Estrategia de despliegue de bajo riesgo y recuperación automática

**Problema:** `docker compose up -d` actualiza el contenedor sin verificar que el nuevo servicio responde correctamente. Si la imagen nueva está rota, el servicio queda caído hasta intervención manual, y GitHub Actions reportaría éxito igualmente.

**Decisión:** Implementar verificación post-despliegue con rollback automático al digest de imagen previo.

**Flujo en EC2 (ejecutado vía SSM):**
1. Antes de actualizar, se captura el digest de la imagen actualmente en ejecución (`docker inspect --format "{{.Image}}"`) como punto de restauración.
2. Se descarga la nueva imagen (`docker compose pull`) y se levantan los contenedores (`docker compose up -d`).
3. Se espera 20 segundos para que el servicio inicialice y luego se realizan hasta 6 intentos de `curl -sf http://localhost:8000/health` con 10 segundos de pausa entre intentos (ventana total: ~80 segundos).
4. Si todos los intentos fallan, se re-etiqueta el digest previo como `latest` y se reinicia el contenedor de API con la imagen anterior. El script termina con `exit 1`, lo que propaga el fallo al job de GitHub Actions.
5. Si el health check pasa, el despliegue se considera exitoso.

**Alternativa descartada:** Guardar el tag SHA del commit anterior y re-descargarlo desde ECR para el rollback. Se descartó porque requiere conocer el SHA previo en el momento del deploy (no disponible directamente en el contexto del script SSM), mientras que el digest local del contenedor en ejecución es siempre accesible sin llamadas externas a ECR.

### Healthcheck en Docker Compose

Se configuró un `healthcheck` en el servicio `api` del `docker-compose.yml`:
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
  interval: 15s
  timeout: 5s
  retries: 3
  start_period: 10s
```

Esto permite que Docker marque el contenedor como `healthy` o `unhealthy` independientemente del estado `running`. Con `restart: unless-stopped`, Docker reinicia automáticamente contenedores que fallen, pero sin healthcheck no distingue entre un proceso corriendo y un servicio respondiendo. El `start_period` de 10s evita falsos negativos durante el arranque de Uvicorn.

## Consecuencias
**Positivas:**
- **Resiliencia ante cambios de infraestructura:** El pipeline sobrevive a reinicios del servidor, ya que SSM encuentra la instancia por su etiqueta, ignorando los cambios de IP pública.
- **Seguridad mejorada:** Cero gestión de claves estáticas o archivos `.pem`; la autenticación se basa en tokens efímeros.
- Consistencia absoluta: No es posible mergear código roto si se exigen los checks en los PRs.
- Cero despliegues manuales; el código aprobado en `main` se refleja automáticamente en el servidor.
- Visibilidad constante de vulnerabilidades de seguridad (vía Trivy) sin bloquear el ritmo de desarrollo.

**Negativas:**
- Mayor complejidad inicial en la configuración de la infraestructura en AWS (creación del proveedor OIDC, políticas JSON y roles IAM específicos para GitHub y la instancia EC2).
- Acoplamiento fuerte al ecosistema de AWS (ECR y SSM), dificultando una posible migración futura a otro proveedor de nube en comparación con el uso de herramientas agnósticas (como SSH y GHCR).

---

## Actualización — Fase 2

**Conteo de tests (línea 30 del contexto):** la suite creció de 16 a 26 tests al incorporar las coberturas de Fase 2:
- `test_forecast_real_dw_id_accepted` — verifica que `/forecast` acepta IDs numéricos reales del DW (PR #81, integración con `gold.dim_pozo`).
- Tests de contratos de auth adicionales (`test_auth.py`): case-insensitivity del header, body del 403.
- Tests de exención de rate limit para `/health` y `/metrics` (`test_rate_limit.py`).
- Tests de cobertura de formato de respuesta y manejo de parámetros inválidos en `test_wells.py` y `test_forecast.py`.

El número "16 tests" en el contexto de este ADR refleja el estado al cierre de Fase 1; la arquitectura de testing (framework, nivel de abstracción, ejecución en CI) no cambió.