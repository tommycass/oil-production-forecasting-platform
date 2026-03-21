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
