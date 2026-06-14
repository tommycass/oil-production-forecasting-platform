# Runbook — Administrador de Gobierno de Datos

**Procedimiento:** Desplegar DataHub en la EC2 de gobierno, ejecutar la ingesta del
linaje dbt y mantener el catálogo de datos actualizado.

Rol de perfil técnico (administrador de gobierno y BI). Complementa
[ADR-017](../adr/0017-plataforma-gobierno-datos.md) (elección de DataHub),
[ADR-018](../adr/0018-orquestacion-end-to-end-dw.md) (orquestación end-to-end) y
el [runbook del Analytics Engineer](analytics-engineer.md) (quién genera los artefactos
dbt que se ingerirán).

---

## 1. Propósito y disparador

Mantener el catálogo de gobierno de datos (DataHub) actualizado con el linaje y la
calidad del pipeline. Se ejecuta cuando:

- **Despliegue inicial:** levantar DataHub por primera vez en la EC2 de gobierno.
- **Actualización del catálogo:** después de cada `dbt build` exitoso (el Analytics
  Engineer corrió el pipeline y generó un `manifest.json` nuevo); ejecutar la ingesta para que el grafo
  de linaje refleje el estado actual.
- **Cambio de modelo dbt:** el Analytics Engineer agregó una tabla nueva, renombró un modelo o modificó
  los tests; actualizar el catálogo para que los usuarios vean el esquema correcto.
- **Incidente de DataHub:** el servidor no responde o la UI muestra datos de una
  corrida anterior.

---

## 2. Rol / dueño y prerrequisitos

- **Dueño:** administrador de infraestructura / gobierno.
- **Accesos requeridos:**
  - Acceso SSH o Session Manager a la EC2 de gobierno (instancia `governance` en AWS).
  - Acceso SSH o Session Manager a la EC2 del pipeline (`api`) donde viven los
    artefactos dbt (`manifest.json`, `run_results.json`).
  - Puertos TCP 8080 (GMS) y 9002 (frontend DataHub) abiertos en el SG de la EC2 de
    gobierno hacia tu IP.
- **Prerrequisitos de la EC2 de gobierno:**
  - Ubuntu 24.04, Docker y Docker Compose instalados.
  - RAM ≥ 8 GB recomendado (DataHub v1.5+ levanta 6 contenedores de larga duración:
    GMS, frontend, MySQL, OpenSearch, Kafka con KRaft integrado y datahub-actions;
    más el contenedor `datahub-system-update` que corre las migraciones al arrancar y
    luego sale con `Exited 0`).
  - Puerto 8080 (GMS) y 9002 (frontend) publicados en el SG de entrada.
- **Prerrequisitos de la EC2 del pipeline (`api`):**
  - `datahub` CLI instalado: `pip install 'acryl-datahub[dbt,datahub-rest]'`
    (el extra `[dbt]` es el que habilita la fuente de ingesta).
  - El pipeline de dbt corrió al menos una vez: existe `manifest.json`.

---

## 3. Pasos

### 3.1 Desplegar DataHub en la EC2 de gobierno (una vez)

> **Atajo:** el script `infra/governance/setup-datahub.sh` automatiza todos los
> pasos de §3.1 y §3.1.1 en un solo comando. Si preferís hacerlo manual, sigue
> las instrucciones a continuación.
>
> ```bash
> # Clonar el repo en la EC2 de gobierno y ejecutar el script:
> git clone <repo-url> oil-production-forecasting-platform
> cd oil-production-forecasting-platform
> chmod +x infra/governance/setup-datahub.sh
> ./infra/governance/setup-datahub.sh
> ```

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

### 3.1.1 Configurar reinicio automático (paso obligatorio post-deploy)

`datahub docker quickstart` levanta los contenedores con la política de reinicio por
defecto de Docker (`no`), lo que significa que **al apagar y volver a prender la
instancia EC2, los contenedores quedan en estado `Exited` y DataHub no vuelve solo**.

Después del primer `quickstart`, configurar `unless-stopped` en los 6 servicios de
larga duración y habilitar Docker en el boot del sistema:

```bash
# Política durable en los 6 servicios de larga duración.
# unless-stopped: se reanudan al prender la instancia, pero respetan un `docker stop`
# manual (útil para mantenimiento). No incluir datahub-system-update: es un job de
# migraciones (Exited 0) que no debe reiniciarse.
docker update --restart unless-stopped \
  datahub-mysql-1 \
  datahub-opensearch-1 \
  datahub-kafka-broker-1 \
  datahub-datahub-gms-quickstart-1 \
  datahub-frontend-quickstart-1 \
  datahub-datahub-actions-quickstart-1

# Asegurar que el daemon de Docker arranque con el SO.
sudo systemctl enable docker
```

Verificar que la política quedó aplicada:

```bash
docker inspect -f '{{.Name}}: {{.HostConfig.RestartPolicy.Name}}' \
  $(docker ps -aq --filter name=datahub)
```

> **Regla operativa:** para apagar la instancia, hacerlo siempre desde la **consola/CLI
> de AWS** (stop de EC2). Los contenedores estaban corriendo → al prenderla se reanudan
> solos. **NO** correr `datahub docker quickstart --stop` ni `docker stop` antes de
> apagar: eso marca los contenedores como "parados intencionalmente" y `unless-stopped`
> —por diseño— no los vuelve a levantar automáticamente.

### 3.2 Ejecutar la ingesta dbt→DataHub

La ingesta lee **tres** artefactos dbt y los envía al GMS de la EC2 de gobierno
**sin tocar el DW**:

- `manifest.json` — grafo de modelos (linaje tabla). **Obligatorio.**
- `catalog.json` — tipos y descripciones de columna (linaje de columna). Obligatorio para linaje de columna; *best-effort* en la corrida automática del pipeline (si `dbt docs generate` falla, la ingesta continúa sin él con `WARN` y no bloquea el pipeline).
- `run_results.json` — resultado de los 31 tests de calidad (assertions). Opcional.

Como el linaje y el esquema se derivan de las **definiciones** de los modelos (no de
los datos), los artefactos pueden generarse contra cualquier base con el modelo
construido. Hay dos caminos:

> **Nota — ingesta recurrente automatizada.** En régimen, **no hace falta correr la
> ingesta a mano**: `run_pipeline.sh` (el cron mensual del ADR-018) la ejecuta tras
> cada `dw_publish` como paso best-effort. Para activarla en una EC2: instalar el CLI
> en el venv del pipeline (`pip install 'acryl-datahub[dbt,datahub-rest]'`) y setear
> `DATAHUB_GMS_HOST` en `infra/.env`. Si DataHub está caído, el paso avisa y no frena
> el pipeline. Los caminos manuales de abajo sirven para el **bootstrap inicial** o una
> **re-ingesta puntual** fuera de ciclo.

**Camino A — desde la EC2 `api` (datos de producción).** Ingesta manual contra el
manifest de prod (equivale a lo que hace el paso automatizado de `run_pipeline.sh`).

```bash
aws ssm start-session --target <instance-id-api> --region us-east-2
sudo -i -u ubuntu
cd /home/ubuntu/oil-production-forecasting-platform

# CLI de DataHub con el extra [dbt] (trae el plugin de la fuente) + [datahub-rest] (sink):
pip install 'acryl-datahub[dbt,datahub-rest]'

# El cron del pipeline genera manifest.json + run_results.json en el dbt build.
# Para el linaje de columna hace falta además catalog.json (un paso extra):
set -a; source infra/.env; set +a
cd transform && dbt docs generate --profiles-dir . && cd ..

# DATAHUB_GMS_HOST debe ser la IP PRIVADA de la EC2 governance (ej: 172.31.23.108),
# no la pública. La IP privada no cambia con stop/start de la instancia (solo si se
# termina y recrea). La IP pública sí cambia en cada arranque; usarla aquí rompería
# la ingesta automática del cron tras cada reinicio de la instancia.
export DATAHUB_GMS_HOST=<ip-privada-ec2-governance>   # ver AWS Console → Private IPv4
# Rutas reales si el Analytics Engineer usa DBT_TARGET_PATH (~/dbt-runtime/target/); si no, transform/target/:
T=/home/ubuntu/oil-production-forecasting-platform/transform/target
datahub ingest -c infra/datahub/dbt_recipe.yml
```

> **Nota sobre `--set`:** el flag `--set` no existe en datahub-cli ≥ 1.6. Las rutas
> del recipe se resuelven relativas al directorio de trabajo; correr el comando desde
> la raíz del repo con las rutas por defecto del recipe (`transform/target/`) es suficiente.
> Si los artefactos están en otra ruta, editá `infra/datahub/dbt_recipe.yml` directamente.

**Camino B — bootstrap desde una máquina con el repo (muestra local).** Útil para la
carga inicial / demo sin depender del cron de prod. El puerto 8080 del GMS está
abierto, así que la ingesta puede correr desde cualquier host:

```bash
# 1. Postgres local de muestra (perfil local-db del compose, o un postgres suelto)
# 2. Sembrar Bronze de muestra y construir el modelo:
python transform/scripts/seed_sample_bronze.py --rows 2000
cd transform && dbt deps && dbt build --profiles-dir . && dbt docs generate --profiles-dir . && cd ..
# 3. Ingestar al GMS remoto (usar IP privada si se corre desde dentro de la VPC;
#    IP pública si se corre desde fuera, ej: laptop local):
export DATAHUB_GMS_HOST=<ip-governance>
datahub ingest -c infra/datahub/dbt_recipe.yml
```

> **Gotcha de rutas:** si el path a `transform/target/` contiene acentos u otros
> caracteres no-ASCII, el parser de la fuente dbt falla al abrir el archivo
> (`FileNotFoundError`). Copiar `target/` a una ruta ASCII y apuntar ahí con `--set`.

Una ingesta exitosa termina con `failures: []` en el reporte del sink y un conteo de
records escritos (`total_records_written`), reportando la `gms_version` del servidor.

### 3.3 Navegar el catálogo y el linaje

Abrir en el navegador: `http://<ip-ec2-gobierno>:9002`

- **Búsqueda:** escribir `fact_produccion_mensual` en la barra de búsqueda para
  acceder a la fact table del modelo estrella.
- **Linaje (tabla):** en la ficha del dataset, hacer clic en la pestaña **Lineage**.
  El grafo muestra `silver_produccion → fact_produccion_mensual` y las dimensiones
  conformadas (`dim_pozo`, `dim_operadora`, etc.). Las vistas del esquema `semantic.*`
  (`sem_top_pozos`, `sem_produccion_mensual_por_yacimiento`, etc.) aparecen como
  nodos **downstream** de la fact y las dims: el linaje cubre Bronze → Silver → Gold → Semantic.
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
| Contenedores en `Exited` al prender la EC2 | Política de restart `no` (no se configuró §3.1.1) | Correr el `docker update --restart unless-stopped` del §3.1.1 y arrancarlos con `docker start $(docker ps -aq --filter name=datahub)` |
| `datahub docker check` muestra contenedores `unhealthy` | OpenSearch o Kafka tardaron en arrancar post-boot | Esperar 3–4 min; GMS puede reiniciarse una vez hasta que sus dependencias queden `healthy`. Si persiste: `datahub docker quickstart --stop && datahub docker quickstart` |
| `Connection refused` al GMS durante la ingesta | El SG de la EC2 de gobierno no tiene el puerto 8080 abierto | Agregar regla de entrada TCP 8080 desde la IP de la EC2 del pipeline (o `0.0.0.0/0` temporalmente para el demo) |
| `FileNotFoundError: manifest.json not found` | dbt no corrió o la ruta es distinta | Verificar que el Analytics Engineer ejecutó `dbt build` y confirmar la ruta real del artefacto en la EC2 del pipeline |
| `Failed to find a registered source for type dbt` | Falta el extra `[dbt]` del CLI | `pip install 'acryl-datahub[dbt,datahub-rest]'` y reintentar |
| Ingesta con errores en la fuente dbt | Versión de datahub CLI incompatible con el schema del manifest | `pip install --upgrade 'acryl-datahub[dbt,datahub-rest]'` y reintentar |
| UI muestra datos de una corrida anterior | El paso de ingesta de `run_pipeline.sh` no corrió (o `DATAHUB_GMS_HOST` no está seteado) | Confirmar que `DATAHUB_GMS_HOST` está en `infra/.env` y el CLI instalado; si no, ejecutar el paso 3.2 manualmente |

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

DataHub (v1.5+) levanta 6 servicios de larga duración: GMS, frontend, MySQL, OpenSearch,
Kafka (modo KRaft, sin ZooKeeper) y datahub-actions. El contenedor `datahub-system-update`
corre migraciones al arrancar y sale con `Exited 0`; es normal y no indica error.

Por defecto, `datahub docker quickstart` deja los contenedores con política `restart: no`.
Para que sobrevivan a un stop/start de la instancia **es obligatorio** correr el
`docker update` del §3.1.1 inmediatamente después del primer deploy. Una vez configurado
`unless-stopped`, al prender la EC2 los contenedores se reanudan solos; durante 2–4 minutos
pueden verse reinicios transitorios del GMS mientras OpenSearch y Kafka terminan de levantar
— es esperado y se estabiliza solo.
