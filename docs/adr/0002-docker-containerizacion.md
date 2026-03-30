# Título: ADR-002: Uso de Docker para contenerización
**Estado:** Aceptado

## Contexto
El equipo trabaja de forma distribuida con un modelo de ramas por funcionalidad y Pull Requests hacia main/develop. Sin automatización, la calidad del código depende de revisiones manuales propensas a error, y el despliegue a nuestro servidor AWS EC2 requiere conectarse manualmente por SSH y ejecutar scripts. Necesitamos garantizar que cada cambio no rompa el sistema, que las imágenes Docker sean seguras y publicadas, y automatizar el despliegue al entorno de desarrollo en AWS de forma continua.

## Decisión
Usaremos **GitHub Actions** para implementar un pipeline secuencial de tres etapas (test → build → deploy), integrado con **GitHub Container Registry (GHCR)** y nuestra instancia de **AWS EC2**.

**Justificación de las herramientas:**
- **GitHub Actions:** Se elige por su integración nativa con el repositorio, evitando configurar y mantener servidores de CI/CD externos (como Jenkins).
- **GHCR:** Permite almacenar las imágenes en el mismo ecosistema de GitHub utilizando el GITHUB_TOKEN automático, evitando los límites de descarga de DockerHub o los costos adicionales de AWS ECR.
- **AWS EC2:** Provee un entorno IaaS (Infraestructura como Servicio) flexible y de bajo costo que nos otorga control total sobre el host de Docker, ideal para esta fase del proyecto en comparación con servicios orquestados más complejos y costosos (como ECS o EKS).

1. **Estrategia de Testing (test):**

- Se utilizan 16 tests automatizados independientes con pytest y TestClient (FastAPI) aislando responsabilidades (health, auth, wells, forecast).
- Se mockea e inyecta la API Key de desarrollo como variable de entorno de forma segura.
- Se incluye análisis estático rápido usando ruff sobre el directorio de producción (api/app/).

2. **Construcción y Registro (build):**

- Se construye la imagen Docker y se escanea con Trivy en modo informativo (exit-code 0 para vulnerabilidades CRITICAL/HIGH).
- Se verifica la salud post-build levantando el contenedor efímeramente y haciendo un GET a /health.
- Se publican las imágenes en GHCR con tags latest y el SHA del commit para permitir rollbacks.

3. **Despliegue Automatizado (deploy):**

- Se ejecuta únicamente al hacer merge a la rama main.
- Se utiliza la action appleboy/ssh-action para conectarse por SSH a la instancia EC2 utilizando credenciales almacenadas en GitHub Secrets (EC2_HOST, EC2_USERNAME, EC2_SSH_KEY).
- El script ingresa al servidor, actualiza el código (git pull) y reinicia los servicios apuntando explícitamente a la configuración de infraestructura (sudo docker compose -f infra/docker-compose.yml pull y up -d).

## Consecuencias
**Positivas:**

- Consistencia y seguridad: no es posible mergear código roto si se exigen los checks en los PRs.
- Cero despliegues manuales; el código aprobado en main se refleja automáticamente en el servidor AWS.
- Trazabilidad completa entre los commits de GitHub y los artefactos desplegados en GHCR.
- Visibilidad constante de vulnerabilidades de seguridad sin bloquear el ritmo de desarrollo.

**Negativas:**

- Mayor complejidad en la configuración y mantenimiento del archivo .github/workflows/ci.yml.
- Dependencia de actions de terceros para la conexión SSH.
- Gestión de IP Dinámica (Punto de falla potencial): Debido a que la instancia EC2 actual es básica y no cuenta con una IP elástica (fija), cada vez que el servidor se apaga y se vuelve a encender, AWS le asigna una nueva Dirección IPv4 pública. Cuando esto ocurre, es **obligatorio actualizar manualmente el valor del secret EC2_HOST en GitHub**; de lo contrario, el job de deploy fallará por timeout al intentar conectarse a una IP inexistente.