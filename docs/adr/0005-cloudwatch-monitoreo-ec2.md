# Título: ADR-005: Integración de AWS CloudWatch como datasource de Grafana para métricas de infraestructura EC2
**Estado:** Aceptado

## Contexto
Prometheus recolecta métricas de la aplicación y los contenedores (vía cAdvisor), pero no tiene acceso nativo a las métricas de la instancia EC2 subyacente que publica AWS: utilización de CPU del host, tráfico de red a nivel de hipervisor, y operaciones de I/O del volumen EBS. Estas métricas son relevantes para distinguir si un problema de rendimiento es de la aplicación o del servidor, y AWS las publica automáticamente en CloudWatch cada 5 minutos sin costo adicional.

## Decisión
Agregar **AWS CloudWatch** como datasource secundario en Grafana, consumiendo las métricas estándar de EC2 (`AWS/EC2`, `AWS/EBS`) mediante el plugin nativo de Grafana. La autenticación se resuelve a través del rol IAM ya asignado a la instancia (`InstanceCDRole`), sin credenciales estáticas.

## Implementación y problemas encontrados

El proceso de integración requirió resolver tres problemas distintos en secuencia:

**1. IMDSv2 y hop limit de Docker**
El contenedor de Grafana necesita obtener credenciales temporales del servicio de metadatos de la instancia (IMDS). La instancia tenía IMDSv2 obligatorio con un límite de saltos HTTP (`HttpPutResponseHopLimit`) de 1. Los contenedores Docker agregan un salto de red extra, por lo que sus requests al IMDS fallaban con 401. La solución fue aumentar el límite a 2 desde la consola de EC2 → Instance Settings → Modify instance metadata options.

**2. Bloqueo por SCP en `oam:ListSinks`**
El plugin de CloudWatch de Grafana intenta descubrir cuentas de monitoreo cross-account llamando a la API `oam:ListSinks`. Esta acción está bloqueada por una Service Control Policy (SCP) a nivel de organización AWS con un `Deny` explícito, que ninguna política de IAM puede sobrepasar. Mientras ese bloqueo existía, el plugin no ejecutaba ninguna query de métricas, mostrando "No data" en todos los paneles sin registrar un error visible. La solución fue deshabilitar el descubrimiento cross-account en el datasource:

```yaml
# monitoring/grafana/provisioning/datasources/cloudwatch.yml
jsonData:
  authType: default
  defaultRegion: us-east-2
  crossAccountQuerying: false
```

**3. Breaking change en Grafana 12: formato de queries CloudWatch**
El problema final y más difícil de identificar: Grafana 12 cambió el formato interno del plugin de CloudWatch de forma incompatible con versiones anteriores. El dashboard usaba el formato viejo, que Grafana 12 ignoraba silenciosamente sin ejecutar las queries ni registrar errores.

| Campo | Formato anterior (roto) | Formato Grafana 12 |
|-------|------------------------|-------------------|
| Estadístico | `"statistics": ["Average"]` | `"statistic": "Average"` |
| Dimensiones | `"dimensions": {"InstanceId": ["i-xxx"]}` | `"dimensions": {"InstanceId": "i-xxx"}` |
| Lenguaje | ausente | `"queryLanguage": "CWLI"` |

El diagnóstico se alcanzó descartando hipótesis sistemáticamente: primero se confirmó que el datasource tenía credenciales válidas (test exitoso), luego que CloudWatch tenía los datos (query directa a la API de Grafana con `curl`), luego que el browser no enviaba ninguna request de CloudWatch (Network tab de DevTools), y finalmente que un panel creado manualmente desde la UI de Grafana sí funcionaba. Comparar el JSON generado por la UI con el JSON del dashboard provisioned reveló las diferencias de formato.

## Consecuencias

**Positivas:**
- Visibilidad completa del servidor: métricas de la aplicación (Prometheus) + métricas del host (CloudWatch) en un único dashboard.
- Sin costo adicional: CloudWatch basic monitoring está habilitado por defecto en todas las instancias EC2.
- Sin agente extra: el plugin de Grafana consulta CloudWatch directamente vía API.

**Negativas:**
- El rol IAM de la instancia necesita la política `CloudWatchReadOnlyAccess`. Si en el futuro se recrea el rol desde cero, hay que recordar agregarla.
- La variable dinámica de selección de instancia (que consultaba CloudWatch para listar los IDs) fue reemplazada por una lista estática hardcodeada, ya que la query de variable también bloqueaba la inicialización del datasource. Si se agregan nuevas instancias al entorno, hay que actualizar manualmente el JSON del dashboard.
- Grafana debe actualizarse con cuidado: el breaking change del plugin de CloudWatch entre versiones mayores no fue comunicado en changelogs obvios y requirió diagnóstico manual.
