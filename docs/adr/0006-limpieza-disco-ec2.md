# Título: ADR-006: Estrategia de limpieza de disco en instancias EC2
**Estado:** Aceptado

## Contexto
Las instancias EC2 (`api` y `api-dev`) acumulaban datos en disco con cada deploy hasta llenar el volumen EBS, provocando que la instancia de desarrollo quedara inoperable el 20 de abril de 2026. El análisis post-mortem identificó dos causas principales:

1. **Imágenes Docker acumuladas:** cada deploy ejecuta `docker compose pull`, que baja una nueva imagen (`oil-forecast-api:latest`) y deja la anterior como imagen sin tag (*dangling*) en `/var/lib/docker/`. Con ~500 MB por imagen y deploys frecuentes, el acumulado fácilmente supera los 10 GB.

2. **Logs de contenedores sin límite:** Docker escribe los logs de cada contenedor en `/var/lib/docker/containers/<id>/*.log` sin rotación ni tope por defecto. La API loguea cada request HTTP y Prometheus scrapea cada 15 segundos, acumulando varios GBs por mes.

No existía ningún mecanismo automático de limpieza; el problema pasó desapercibido porque tampoco había alerta de uso de disco configurada.

## Decisión

Implementar dos medidas complementarias que se activan dentro del flujo de CI/CD existente, sin introducir agentes externos ni cron jobs fuera del repositorio.

**1. Limpieza de imágenes post-deploy en `ci.yml`**

Agregar `docker image prune -af` como último comando del script SSM en los jobs `deploy` y `deploy_dev`, ejecutándose únicamente tras un health check exitoso:

```bash
"docker image prune -af",
"echo \"Docker image cleanup complete.\""
```

Este comando elimina todas las imágenes que no están referenciadas por ningún contenedor en ejecución en ese momento. Corre después del health check para garantizar que la nueva imagen ya está activa y no será eliminada. Si el health check falla (y el script termina en `exit 1` antes del prune), la imagen vieja se preserva para el rollback automático ya implementado.

**2. Rotación de logs en `docker-compose.yml`**

Configurar el driver de logs `json-file` con límites en todos los servicios:

| Servicio | max-size | max-file | Techo total |
|---|---|---|---|
| api | 50m | 3 | 150 MB |
| prometheus | 50m | 3 | 150 MB |
| grafana | 20m | 2 | 40 MB |
| cadvisor | 20m | 2 | 40 MB |
| alertmanager | 20m | 2 | 40 MB |

La configuración se aplica al recrear el contenedor, por lo que toma efecto automáticamente en el siguiente deploy.

## Alternativas descartadas

**`daemon.json` con log rotation global:** Configurar `/etc/docker/daemon.json` en la instancia aplicaría los límites a todos los contenedores presentes y futuros. Se descartó porque requiere `systemctl restart docker` para activarse, lo que tiraría todos los contenedores en ejecución. Aplicarlo desde el CI implicaría coordinar un reinicio controlado, agregando complejidad innecesaria. La configuración por servicio en `docker-compose.yml` logra el mismo resultado de forma segura y versionada.

**Cron job de `docker system prune` en EC2:** Un timer de systemd o cron semanal podría complementar la limpieza. Se descartó como medida principal porque queda fuera del repositorio y del pipeline, dificultando su auditoría y reproducción en nuevas instancias. La limpieza post-deploy cubre el mismo caso con mejor trazabilidad.

**Aumentar el volumen EBS:** Expandir de 8 GB a 20-30 GB posterga el problema sin resolverlo. Se descartó como solución única.

## Consecuencias

**Positivas:**
- Cada deploy deja la instancia con una sola imagen de API en disco (la que está corriendo), eliminando el acumulado histórico.
- Los logs tienen un techo garantizado de ~420 MB en total para todos los servicios, independientemente del tiempo de ejecución.
- La solución vive íntegramente en el repositorio: los cambios son visibles en code review y se reproducen automáticamente en cualquier nueva instancia.

**Negativas:**
- Tras un deploy exitoso, la imagen anterior ya no está disponible localmente para rollback manual. Si se necesita revertir después de que el CI terminó, hay que bajar la imagen desde ECR usando el tag `sha-<commit>` que el pipeline publica en cada build.
- La rotación de logs en contenedores ya existentes toma efecto recién cuando esos contenedores se recrean (próximo deploy). Los logs acumulados previos al cambio deben limpiarse manualmente si ya ocupan espacio significativo.
