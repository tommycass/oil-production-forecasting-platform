# Título: ADR-004: Sistema de Notificaciones de Incidentes con Alertmanager y Slack
**Estado:** Aceptado

## Contexto
El sistema cuenta con métricas críticas recopiladas por Prometheus y visualizadas permanentemente por Grafana, y además tiene cargadas diversas reglas declarativas (`alerts.yml`) sobre umbrales inaceptables como tasa de fallas HTTP o caída total de la instancia (Uptime=0). Sin embargo, se requiere de un motor capaz de rutear, despachar y diseminar activamente avisos ante estos eventos (Notificaciones Push) hacia un canal de respuesta a incidentes del equipo, siendo Slack la herramienta corporativa elegida.

## Análisis de Alternativas
Se evaluaron los dos estándares dominantes de la industria dentro del ecosistema actual:
1. **Grafana Alerting:** Provee una interfaz gráfica de usuario (UI) amigable y no requiere un contenedor adicional. Alertas basadas en los paneles visuales.
2. **Prometheus Alertmanager:** Herramienta oficial y nativa del ecosistema de recolección de Prometheus dedicada unicamente al enrutamiento, inhibición y agrupamiento de incidentes. Toda su configuración ocurre atómicamente a través de archivos de texto (`YAML`).

## Decisión
Se decidió implementar **Prometheus Alertmanager** sobre Grafana Alerting fundamentalmente por su adherencia al paradigma de **Infraestructura como Código (IaC)**. Puesto que el proyecto busca estar completamente orquestado a través de despliegues automatizados (GitOps vía GitHub Actions), requerir intervenciones o puestas a punto manuales haciendo clics en interfaces web para configurar Webhooks no era una práctica robusta recomendada. Alertmanager permite que tanto las reglas como sus receptores sean declarados y auto-desplegados instantáneamente con el encendido de los contenedores.

## Consecuencias
**Positivas:**
- Adherencia total a Infraestructura como Código (GitOps).
- Integración pura y nativa de las métricas crudas en `metrics.yml` ya previamente inyectadas en Prometheus.
- Prevención automatizada de sobrepoblación y colapsos de mensajería (Spam/Throttling) al contar con parámetros como `group_wait` y `repeat_interval` por defecto.

**Negativas:**
- Se agrega una capa adicional de sobrecarga (overhead) estructural al requerir de un servicio/contenedor y volumen perpetuos adicionales corriendo bajo Docker Compose.

## Decisiones Técnicas Posteriores
- **Estrategia Clandestina de Credenciales (Secrets):** 
Para prevenir que la URL privada del Incoming Webhook de Slack se filtre o sea traqueada dentro de la historia del control de versiones (Git), se optó por ignorar el hardcoding clásico (`api_url`). En su lugar, se utilizó la propiedad de resolución externa nativa profunda `api_url_file`, ordenando al servicio buscar físicamente una vez arrancado un archivo llamado `.slack_webhook`. La exclusión explícita de este archivo fue fijada forzosamente garantizando la integridad de seguridad perimetral dentro del `.gitignore`.
- **Inyección por Integración/Despliegue Continuo (CI/CD):** 
Como el archivo `.slack_webhook` queda excluido del repositorio, para que AWS sea capaz de iniciar el programa sin errores se extendió el script de la automatización final del CD mediante AWS Systems Manager (SSM) en `.github/workflows/ci.yml`. GitHub asume el rol del aprovisionador enviando dinámicamente un comando nativo del sistema para fabricar dicho archivo plano utilizando su bóveda criptográfica de `secrets` inyectando el enlace hacia la instancia productiva previo a ejecutar el levante del contenedor, garantizando compatibilidad desatendida.
- **Buffer Analítico de Notificaciones (Throttling):**
Para mitigar la "Fatiga de Alertas" (Alert Fatigue), se configuraron dentro del `alertmanager.yml` políticas explícitas de contención de tráfico: `group_wait` (10s) permite agrupar fallas en lote si múltiples microservicios se caen en cadena simultáneamente, `group_interval` (5m) actúa controlando la ráfaga de envíos, y `repeat_interval` (1h) previene el bombardeo continuo si un servidor permanece caído y el operador ya notificó que está atacando el incidente, generando un flujo de ruido hacia Slack extremadamente aséptico y controlable.
