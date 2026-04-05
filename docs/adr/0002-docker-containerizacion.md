# Título: ADR-002: Uso de Docker para contenerización
**Estado:** Aceptado

## Contexto
El equipo trabaja de forma distribuida con un modelo de ramas por funcionalidad y Pull Requests hacia main/develop. Sin automatización, la calidad del código depende de revisiones manuales propensas a error, y el despliegue a nuestro servidor AWS EC2 requiere conectarse manualmente por SSH y ejecutar scripts. Necesitamos garantizar que cada cambio no rompa el sistema, que las imágenes Docker sean seguras y publicadas, y automatizar el despliegue al entorno de desarrollo en AWS de forma continua.

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

**3. Despliegue Automatizado (`deploy`):**
- Se ejecuta únicamente al hacer merge a la rama `main`.
- Se utiliza SSM (`aws ssm send-command`) apuntando a la instancia con la etiqueta `Name=api`.
- El script ingresa al servidor, autentica el demonio de Docker con ECR, actualiza el código (`git pull`) y reinicia los servicios (`sudo docker compose -f infra/docker-compose.yml pull` y `up -d`).

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