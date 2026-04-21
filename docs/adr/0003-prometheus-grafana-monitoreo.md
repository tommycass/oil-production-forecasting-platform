# Título: ADR-003: Uso de Prometheus + Grafana para monitoreo
**Estado:** Aceptado

## Contexto
El sistema necesita monitorear latencia, cantidad de peticiones, tasa de errores y disponibilidad de la API en tiempo real. Aunque existen soluciones SaaS como DataDog o New Relic, éstas pueden implicar altos costos, y alternativas on-premise como Elastic Stack pueden resultar muy pesadas sólo para métricas numéricas y generación de alertas.

## Decisión
Usaremos **Prometheus y Grafana** porque conforman el estándar open-source de la industria para el monitoreo de métricas. Prometheus incluye un sistema de recolección de métricas robusto (compatible con nuestra API expuesta) y alertas, mientras que Grafana permite visualizar esos datos de manera muy flexibile sin costos de licenciamiento.

## Consecuencias
**Positivas:**
- Solución sin costo de licencias, completamente open-source.
- Integración nativa excelente con Docker y componentes en Python (FastAPI).
- Flexibilidad total para personalizar dashboards operacionales y alertas.

**Negativas:**
- Requiere mantenimiento y configuración manual de la infraestructura subyacente (archivos YAML, persistencia de volúmenes).
- No resuelve la centralización de logs sin añadir piezas adicionales (como Loki u otras herramientas).

## Decisiones Técnicas Posteriores
- **Exclusión de Métricas Propias:** Al instrumentar FastAPI, se optó por excluir explícitamente el registro de las llamadas al endpoint `/metrics` (`excluded_handlers=["/metrics"]`). Esto evita que los *scrapes* periódicos de Prometheus influyan y distorsionen artificialmente las métricas de negocio ("Volumen de Consultas") de la API.
- **Visualización de Disponibilidad (Uptime):** Se eliminó el gráfico de fondo de la estadística general de estado ("UP" / "DOWN") para aportar mayor claridad inmediata. El historial cronológico de desconexiones ahora se audita de forma explícita mediante un nuevo panel especializado del tipo "State Timeline", el cual permite medir con precisión la duración y los intervalos de las caídas de latencia.
- **Acceso Directo al Dashboard (Modo Kiosko):** Se configuró la variable de entorno `GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH` en el contenedor de Grafana para sobrescribir la pantalla de inicio por defecto. De este modo, tras el login, los operarios son redirigidos inmediatamente al dashboard operacional, reduciendo clics y optimizando la navegación.
- **Auto-detección de la Instancia EC2 (IMDS):** Se decidió no exponer el identificador de la máquina de staging al usuario final. Para que la misma imagen y el mismo `docker-compose.yml` muestren métricas distintas según el host, el dashboard se versiona como template (`api-metrics.json.tpl`) con el placeholder `__EC2_INSTANCE_ID__`. Un init container (`grafana-init`) consulta el Instance Metadata Service (IMDSv2) en `http://169.254.169.254`, obtiene el `instance-id` del host y lo sustituye con `sed` sobre un volumen compartido antes de que arranque Grafana. Fallback opcional a la variable `EC2_INSTANCE_ID` si IMDS no responde (útil para levantar local, donde el dashboard queda sin datos de CloudWatch).
- **Filtro de Instancia Oculto:** La variable `instance` del dashboard se dejó con `"hide": 2`, lo que elimina el dropdown de la barra superior. El usuario final no ve ni puede elegir entre staging y producción: cada Grafana muestra únicamente la máquina donde corre.
- **Lockdown Kiosko Real (Usuario Viewer + Restricciones):** Además de redirigir al dashboard, se blindó la UI para evitar ediciones accidentales o navegación fuera del panel. Se provisiona automáticamente un usuario de solo lectura (`ext_read` / `visitor123`) mediante un init container (`grafana-user-init`) que espera a que Grafana esté up y crea el usuario vía `POST /api/admin/users`; el rol por defecto queda en `Viewer` por `GF_USERS_AUTO_ASSIGN_ORG_ROLE`. Se deshabilitaron también el signup (`GF_USERS_ALLOW_SIGN_UP=false`), la edición por viewers (`GF_USERS_VIEWERS_CAN_EDIT=false`), la pestaña Explore (`GF_EXPLORE_ENABLED=false`), los snapshots externos (`GF_SNAPSHOTS_EXTERNAL_ENABLED=false`) y el menú de signout (`GF_AUTH_DISABLE_SIGNOUT_MENU=true`). El usuario externo accede siempre vía URL con `?kiosk=true`, lo que oculta toda la barra lateral y superior.
