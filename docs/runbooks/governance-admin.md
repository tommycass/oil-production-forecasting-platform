# Runbook — Administrador de Gobierno de Datos

**Procedimiento:** Desplegar DataHub en la EC2 de gobierno, ejecutar la ingesta del
linaje dbt y mantener el catálogo de datos actualizado.

Rol de perfil técnico (Persona C). Complementa
[ADR-017](../adr/0017-plataforma-gobierno-datos.md) (elección de DataHub),
[ADR-018](../adr/0018-orquestacion-end-to-end-dw.md) (orquestación end-to-end) y
el [runbook del Analytics Engineer](analytics-engineer.md) (quién genera los artefactos
dbt que se ingerirán).

---

## 1. Propósito y disparador

Mantener el catálogo de gobierno de datos (DataHub) actualizado con el linaje y la
calidad del pipeline. Se ejecuta cuando:

- **Despliegue inicial:** levantar DataHub por primera vez en la EC2 de gobierno.
- **Actualización del catálogo:** después de cada `dbt build` exitoso (B corrió el
  pipeline y generó un `manifest.json` nuevo); ejecutar la ingesta para que el grafo
  de linaje refleje el estado actual.
- **Cambio de modelo dbt:** B agregó una tabla nueva, renombró un modelo o modificó
  los tests; actualizar el catálogo para que los usuarios vean el esquema correcto.
- **Incidente de DataHub:** el servidor no responde o la UI muestra datos de una
  corrida anterior.

---

## 2. Rol / dueño y prerrequisitos

- **Dueño:** administrador de infraestructura / Persona C.
- **Accesos requeridos:**
  - Acceso SSH o Session Manager a la EC2 de gobierno (instancia `governance` en AWS).
  - Acceso SSH o Session Manager a la EC2 del pipeline (`api`) donde viven los
    artefactos dbt (`manifest.json`, `run_results.json`).
  - Puertos TCP 8080 (GMS) y 9002 (frontend DataHub) abiertos en el SG de la EC2 de
    gobierno hacia tu IP.
- **Prerrequisitos de la EC2 de gobierno:**
  - Ubuntu 22.04, Docker y Docker Compose instalados.
  - RAM ≥ 4 GB (DataHub levanta ~8 contenedores: GMS, frontend, Kafka, ZooKeeper,
    Elasticsearch, MySQL, schema-registry, datahub-upgrade).
  - Puerto 8080 (GMS) y 9002 (frontend) publicados en el SG de entrada.
- **Prerrequisitos de la EC2 del pipeline (`api`):**
  - `datahub` CLI instalado: `pip install 'acryl-datahub[datahub-rest]'`.
  - El pipeline de dbt corrió al menos una vez: existe `manifest.json`.

---

## 3. Pasos

### 3.1 Desplegar DataHub en la EC2 de gobierno (una vez)

Conectarse a la EC2 de gobierno vía Session Manager:

```bash
aws ssm start-session --target <instance-id-governance> --region us-east-2
```

Instalar el CLI de DataHub (que incluye el quickstart manager):

```bash
pip install 'acryl-datahub[datahub-rest]'
datahub version
```

Levantar el stack completo con quickstart. El primer arranque descarga ~3 GB de
imágenes y tarda 5–10 minutos:

```bash
datahub docker quickstart
```

Cuando el comando termine y todos los contenedores estén `healthy`, la UI está en:
`http://<ip-ec2-gobierno>:9002`  — usuario `datahub`, contraseña `datahub`.

> El GMS (API) queda en `http://<ip-ec2-gobierno>:8080`.

Para verificar que el stack está sano:

```bash
datahub docker check
```

### 3.2 Ejecutar la ingesta dbt→DataHub

La ingesta lee los artefactos dbt (`manifest.json`, `run_results.json`) que B genera
en la EC2 del pipeline (`api`) y los envía al GMS de la EC2 de gobierno.

Conectarse a la EC2 del pipeline:

```bash
aws ssm start-session --target <instance-id-api> --region us-east-2
sudo -i -u ubuntu
cd /home/ubuntu/oil-production-forecasting-platform
```

Si `datahub` CLI no está instalado en esta EC2:

```bash
pip install 'acryl-datahub[datahub-rest]'
```

Configurar el endpoint del GMS y ejecutar la ingesta:

```bash
export DATAHUB_GMS_HOST=<ip-ec2-governance>

# Ubicación real de los artefactos dbt en EC2 (si B usa DBT_TARGET_PATH):
MANIFEST=/home/ubuntu/dbt-runtime/target/manifest.json
RUN_RESULTS=/home/ubuntu/dbt-runtime/target/run_results.json

datahub ingest -c infra/datahub/dbt_recipe.yml \
  --set source.config.manifest_path="$MANIFEST" \
  --set source.config.run_results_path="$RUN_RESULTS"
```

Una ingesta exitosa imprime algo similar a:

```
✅  Ingestion completed with warnings: 0, errors: 0
Source report: ... entities ingested: ...
Sink report: ... records written: ...
```

### 3.3 Navegar el catálogo y el linaje

Abrir en el navegador: `http://<ip-ec2-gobierno>:9002`

- **Búsqueda:** escribir `fact_produccion_mensual` en la barra de búsqueda para
  acceder a la fact table del modelo estrella.
- **Linaje (tabla):** en la ficha del dataset, hacer clic en la pestaña **Lineage**.
  El grafo muestra `silver_produccion → fact_produccion_mensual` y las dimensiones
  conformadas (`dim_pozo`, `dim_operadora`, etc.).
- **Calidad:** la pestaña **Assertions** lista los 31 tests dbt con su último estado
  (`pass`/`fail`) derivado de `run_results.json`.
- **Última actualización:** la pestaña **Timeline** muestra cuándo ocurrió la última
  ingesta y qué cambió en el modelo.

---

## 4. Validación

La ingesta y el catálogo están correctos cuando:

- `datahub ingest` termina con `errors: 0`.
- En la UI, buscar `fact_produccion_mensual`: la ficha aparece, muestra la plataforma
  `postgres` y el entorno `PROD`.
- La pestaña **Lineage** muestra al menos 3 upstream (los modelos `silver_*` que
  alimentan la fact).
- La pestaña **Assertions** lista ≥ 5 tests (idealmente los 31 checks de dbt).
- El campo **Last Updated** refleja la fecha de la última corrida del pipeline.

---

## 5. Si algo falla

| Síntoma | Causa probable | Acción |
|---|---|---|
| `datahub docker check` muestra contenedores `unhealthy` | RAM insuficiente o Elasticsearch tardó en arrancar | Esperar 2 min más; si persiste: `datahub docker quickstart --stop && datahub docker quickstart` |
| `Connection refused` al GMS durante la ingesta | El SG de la EC2 de gobierno no tiene el puerto 8080 abierto | Agregar regla de entrada TCP 8080 desde la IP de la EC2 del pipeline (o `0.0.0.0/0` temporalmente para el demo) |
| `FileNotFoundError: manifest.json not found` | dbt no corrió o la ruta es distinta | Verificar que B ejecutó `dbt build` y confirmar la ruta real del artefacto en la EC2 del pipeline |
| Ingesta con errores en la fuente dbt | Versión de datahub CLI incompatible con el schema del manifest | `pip install --upgrade 'acryl-datahub[datahub-rest]'` y reintentar |
| UI muestra datos de una corrida anterior | La ingesta no se ejecutó tras el último `dbt build` | Ejecutar el paso 3.2 manualmente; a futuro, agregar `datahub ingest ...` al script de cron del pipeline |
| DataHub requiere más de 4 GB y la instancia se queda sin RAM | El quickstart levanta ~8 contenedores pesados | Detener contenedores no usados en esa EC2, ampliar swap, o migrar a instancia `t3.large` |

---

## 6. Consideraciones no funcionales

### Frescura del catálogo

El catálogo de DataHub **no es en tiempo real**: refleja el estado del último `dbt build`
que tuvo una ingesta exitosa. Hay una latencia acumulada: el pipeline corre mensualmente
(cron el 5 de cada mes), y la ingesta a DataHub se dispara manualmente o como paso del
mismo script. En consecuencia, el linaje en DataHub puede tener hasta un mes de retraso
respecto del estado real del modelo. Esto es aceptable para gobierno estratégico (auditoría,
documentación del linaje), pero no para monitoreo operativo en tiempo real.

**Decisión funcional:** la ingesta a DataHub lee **solo los artefactos dbt**
(`manifest.json`, `run_results.json`), sin conectar directamente al RDS. Esto significa
que el linaje es **reproducible offline** desde los artefactos ya generados, sin abrir
accesos adicionales al RDS (que vive dentro de la VPC y es accesible únicamente desde los
SG de las EC2 del pipeline). La alternativa — añadir un conector Postgres que consulte
el warehouse en vivo — agregaría linaje de columna más preciso para las vistas y funciones
SQL, pero requeriría abrir una regla de SG adicional del EC2 de gobierno al RDS, aumentar
la superficie de ataque, y mantener una conexión privilegiada al DW de producción. El
trade-off favorece reproducibilidad y seguridad sobre exhaustividad del linaje de columna,
que puede cubrirse parcialmente añadiendo `catalog_path` al recipe cuando se incluya
`dbt docs generate` en el pipeline.

**Decisión no funcional:** se usa `datahub docker quickstart` en lugar de un
`docker-compose.yml` propio mantenido en el repo. El quickstart descarga y gestiona el
compose oficial de DataHub (versión publicada por el proyecto), lo que elimina la carga
de mantener sincronizada la versión del compose con la del CLI. El costo es menor control
sobre la versión exacta de cada contenedor; en un entorno de demo/académico donde las
actualizaciones son infrecuentes, esa falta de control es aceptable a cambio de setup en
2 comandos (`pip install` + `datahub docker quickstart`). En producción real con SLA, el
paso correcto sería fijar las versiones en un compose propio y gestionarlo como IaC.

### Seguridad

La instalación por defecto de DataHub quickstart usa credenciales `datahub/datahub` sin
HTTPS. Para el entorno académico es aceptable; para producción real: cambiar contraseña
del admin, habilitar TLS (reverse proxy nginx/caddy frente al frontend) y restringir el
puerto 9002 a la red interna. El GMS (8080) nunca debe exponerse públicamente en producción.

### Disponibilidad

DataHub depende de ~8 contenedores. Si la EC2 de gobierno se reinicia, el quickstart
registra los contenedores con política `restart: always`, por lo que reanudan
automáticamente. Si Elasticsearch tarda en arrancar (frecuente al reiniciar), el GMS
puede responder lento durante 1–2 minutos — esperar antes de concluir que está caído.
