# Título: ADR-038: Backend del servidor MLflow para tracking y model registry

**Estado:** Propuesta

> Relacionado con [ADR-030](0030-plataforma-tracking-experimentos.md) (elección de MLflow) y [ADR-002](0002-docker-containerizacion.md) (Docker como runtime). ADR-030 decidió usar MLflow y delegó la infraestructura del servidor al Rol 3; este ADR documenta esa decisión de infraestructura.

---

## Contexto

ADR-030 eligió MLflow como plataforma de tracking y model registry. El Rol 1 lo usa localmente con un backend SQLite (`sqlite:///mlflow.db`) que es suficiente para desarrollo individual pero no para el escenario de entrega:

- **El servidor de tracking debe ser accesible** tanto por el script de entrenamiento (Rol 1) como por la API de inferencia (Rol 3), que corren en procesos distintos y potencialmente en hosts distintos.
- **El model registry necesita persistencia** más allá de archivos locales: si el servidor se reinicia, el historial de versiones y el estado `Production`/`Staging` de los modelos no debe perderse.
- **La API carga el modelo por stage** (`Production`), lo que requiere un registry operativo con soporte de stages.

Hay que decidir cuál es el backend de almacenamiento del servidor MLflow: dónde se guardan los metadatos de runs y el model registry.

---

## Análisis de Alternativas

### Alternativa A — SQLite compartido (archivo en volumen Docker)

Usar `sqlite:///mlflow.db` montado en un volumen Docker. Simple, cero dependencias nuevas.

**Descartada:** SQLite no soporta acceso concurrente. Si el script de entrenamiento y la API consultan el servidor simultáneamente —a través del servidor MLflow— no hay problema, pero si hay más de un proceso escribiendo directamente al archivo SQLite, hay corrupción. Además, escala mal y el file-lock de SQLite puede fallar en volúmenes de red.

### Alternativa B — Postgres como backend (seleccionada)

Usar la instancia de Postgres ya presente en el proyecto (`oil_dw` en local, RDS en staging/prod) con una base de datos separada `mlflow_db`. MLflow soporta Postgres como backend de metadatos con `psycopg2`.

**Ventajas:**
- **Sin infraestructura nueva:** la instancia de Postgres ya existe para el DW; solo hay que crear una base adicional (`CREATE DATABASE mlflow_db`).
- **Concurrencia y durabilidad** nativos de Postgres.
- **Persistencia entre reinicios** del contenedor MLflow.
- Mismo patrón que `metabase_app` (base separada para la metadata de Metabase en la misma instancia).

**Desventajas:**
- Requiere que `psycopg2-binary` esté instalado en el contenedor MLflow (se hace en el `command` del servicio).
- Se crea una base adicional que hay que incluir en el backup del RDS.

### Alternativa C — MLflow gestionado (Databricks, AWS SageMaker, etc.)

Delegar el servidor a una plataforma cloud.

**Descartada:** introduce costos, dependencias externas y va contra el criterio de self-hosted del proyecto (mismo argumento que en ADR-003 y ADR-030).

---

## Decisión

**Postgres como backend del servidor MLflow**, con una base `mlflow_db` separada de `oil_dw` en la misma instancia.

- **Artefactos:** almacenados en un volumen Docker local (`mlartifacts`) y servidos vía HTTP con el flag `--serve-artifacts`. Esto permite que la API de inferencia descargue modelos por HTTP sin montar el mismo volumen.
- **Imagen:** `ghcr.io/mlflow/mlflow:v2.17.0` (official, para el serving tier de producción; el Rol 1 puede usar cualquier versión compatible localmente).
- **Perfil Docker:** `ml` — opcional, exactamente igual que `bi` (Metabase) y `orchestration` (Dagster). Para activarlo en la EC2 `api`, se agrega `ml` al `COMPOSE_PROFILES` del `api/.env` de la instancia. El deploy script existente (`docker compose up -d`) lo levanta automáticamente al releer el `.env`.
- **Red:** corre en la misma EC2 `api` (18.116.35.133). La API de inferencia llega a MLflow por red Docker interna (`http://mlflow:5000`), por lo que `MLFLOW_TRACKING_URI=http://mlflow:5000` va en el `api/.env` de la instancia.
- **Base de datos:** requiere una base `mlflow_db` en el RDS existente, creada una sola vez: `CREATE DATABASE mlflow_db;`. El `POSTGRES_HOST` del `.env` ya apunta al RDS correcto.
- **Configuración:** `MLFLOW_TRACKING_URI` como variable de entorno. Si no está seteada (desarrollo local sin el perfil `ml`), `ml/config.py` cae a SQLite local — el flujo del Rol 1 se preserva sin cambios.
- **Puerto:** 5000 (accesible externamente si se abre en el security group de la EC2, para acceder a la UI de MLflow desde el navegador; no requerido para que la API funcione).

## Consecuencias

**Positivas:**
- El training (Rol 1) y la API (Rol 3) apuntan al mismo servidor y ven el mismo model registry.
- El historial de experimentos y las versiones del modelo persisten entre reinicios del contenedor.
- Cero dependencias nuevas de infraestructura.

**Negativas:**
- Hay que crear la base `mlflow_db` en Postgres antes de arrancar el servicio (`CREATE DATABASE mlflow_db` en el RDS de staging/prod).
- El contenedor MLflow instala `psycopg2-binary` al arrancar (pip install en el command), lo que agrega ~5s de startup. Mitigación futura: buildear una imagen custom con psycopg2 preinstalado.
