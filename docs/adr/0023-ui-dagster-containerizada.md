# Título: ADR-023: UI de Dagster containerizada (complementa ADR-018)

**Estado:** Aceptado

> Complementa al [ADR-018](0018-orquestacion-end-to-end-dw.md); **no lo supersede**. La
> decisión central del 018 —el pipeline de producción se dispara por **cron headless**—
> sigue vigente. Este ADR solo agrega la **UI de Dagster** como herramienta de
> observabilidad.

## Contexto

El ADR-018 eligió correr Dagster **headless por cron**, sin webserver ni daemon
persistentes, porque las EC2 de la API (2–4 GB) ya corrían la API + monitoreo y no
tenían RAM para un servicio más. Ese ADR dejó la falta de UI como una negativa
**explícitamente reversible**: *"si la instancia se agranda, se puede volver al daemon +
schedule nativo sin tocar los assets"* y *"la observabilidad se reduce a logs…
mitigable activando el daemon si se agranda la instancia"*.

Esa condición ya se cumple: el despliegue de gobierno usa una EC2 **`t3.large` (8 GB)**
([ADR-017](0017-plataforma-gobierno-datos.md)), y el entorno local de desarrollo
también tiene capacidad de sobra. Además, `data_pipeline/requirements.txt` ya declara
`dagster-webserver`: la UI nunca fue un problema de dependencias, sino de footprint
operativo. Queda decidir **cómo** se expone la UI sin romper las restricciones que
motivaron el ADR-018 (no cargar la EC2 chica de `api`, no tocar los assets, no alterar
el disparo de producción).

### Evaluación de alternativas — cómo exponer la UI

| Criterio | Servicio compose con `dagster dev` (perfil) | `dagster-webserver` + `dagster-daemon` separados | Solo venv en el host (`dagster dev` manual) |
|---|---|---|---|
| Reproducibilidad (imagen versionada) | Alta (Dockerfile en el repo) | Alta | Baja (setup manual del venv) |
| Simpleza (definiciones de servicio) | 1 servicio | 2 servicios + `dagster.yaml` | 0, pero no gestionado |
| Aislamiento del stack por defecto | Sí (perfil `orchestration`) | Sí (perfil) | N/A |
| Schedules nativos de Dagster en serio | No (basta para demo/obs.) | Sí | No |
| Encaje con "no tocar assets / prod = cron" | Total | Total | Total |
| Costo de mantenimiento | Bajo | Medio | Bajo pero no portable |

### Por qué este camino

`dagster dev` corre **webserver + daemon en un solo proceso** y, al setear
`DAGSTER_IS_DEV_CLI`, regenera el `manifest.json` de dbt al arrancar
(`prepare_if_dev → dbt parse`), de modo que los modelos Silver/Gold aparecen como assets
sin pasos manuales. A la escala actual eso da toda la observabilidad buscada (grafo de
assets, logs, status, materialización on-demand) con **una sola definición de servicio**.
Separar `webserver` + `daemon` con storage persistente (`dagster.yaml`) solo se justifica
si se quisieran correr **schedules nativos de Dagster en producción** — pero producción
usa el cron del SO (ADR-018), así que esa complejidad no aporta hoy.

## Decisión

1. **Servicio `dagster` en `infra/docker-compose.yml`**, construido desde
   `infra/Dockerfile.dagster` (Python 3.11 + `data_pipeline` + `transform` + `dbt deps`),
   corriendo `dagster dev -m data_pipeline.orchestration.definitions`.
2. **Profile-gated (`orchestration`).** No arranca con un `docker compose up` por defecto
   ni entra en el build de CI (que solo construye la imagen de la API). Se levanta
   deliberadamente donde haya capacidad (`--profile orchestration`), evitando cargar la
   EC2 chica de `api` por accidente — preservando la restricción que motivó el ADR-018.
3. **Puerto 3070** en el host (el 3000 lo usa Grafana). `DAGSTER_HOME` en un volumen
   (`dagster_home`) para conservar el historial de runs.
4. **No reemplaza el disparo de producción.** El cron mensual headless del ADR-018 sigue
   siendo la fuente de la materialización en prod; esta UI es para **observabilidad y
   materializaciones on-demand**, no para schedules productivos.
5. **No se toca el código de orquestación.** El servicio reusa
   `data_pipeline.orchestration.definitions` tal cual — exactamente la reversibilidad
   "sin tocar los assets" que el ADR-018 anticipó.

## Consecuencias

**Positivas:**
- Se resuelve la única negativa operativa del ADR-018 (falta de UI) en los entornos con
  capacidad, sin reescribir su decisión ni el código de assets.
- Reproducible: la UI es una imagen versionada en el repo, no un setup manual de venv.
- Aislada: el perfil evita impactar el stack por defecto y el CI.
- Bonus de DX: en local da la UI de Dagster en dos comandos
  (`--profile orchestration --profile local-db`), sin armar venv a mano.

**Negativas:**
- La imagen es pesada (dagster + dbt + pandas + pyarrow); el primer build tarda varios
  minutos.
- `dagster dev` no es el modo "productivo" para correr schedules; si algún día se quieren
  schedules nativos en vez del cron del SO, habrá que separar `webserver` + `daemon` con
  `dagster.yaml` (queda como evolución, no es necesario hoy).
- Materializar desde la UI requiere que el contenedor tenga `POSTGRES_*` y el volumen
  `data/` montados (ya provisto en el compose); para solo **ver** el grafo no hace falta.

## Verificación

- `docker compose --profile orchestration --profile bi --profile local-db config`: válido.
- `docker build -f infra/Dockerfile.dagster`: OK (incluye `dbt deps`).
- `dbt parse` + `dagster definitions validate -m data_pipeline.orchestration.definitions`
  dentro de la imagen: *"All code locations passed validation"* (carga el grafo completo,
  Bronze + modelos dbt).

## Actualización (jun-2026) — implementación en `api-dev` via systemd

En `api` (producción), el venv de Dagster (`~/dagster-venv`) ya estaba instalado para
el cron headless (ADR-018) y la imagen Docker nunca fue construida. En lugar de buildear
la imagen, la UI se expone mediante un **servicio systemd** (`/etc/systemd/system/dagster-ui.service`)
que invoca `dagster dev` directamente sobre el venv existente:

```
ExecStart=/home/ubuntu/dagster-venv/bin/dagster dev \
  -m data_pipeline.orchestration.definitions --host 0.0.0.0 --port 3070
```

Variables clave del servicio: `EnvironmentFile=infra/.env`, `DAGSTER_HOME=/home/ubuntu/dagster-runtime`,
`PATH` extendido con el bin del venv (necesario para que `DbtCliResource` resuelva el
ejecutable `dbt`). El servicio está habilitado con `systemctl enable`, por lo que
**reinicia automáticamente en cada boot de EC2** — equivalente operativo del
`restart: unless-stopped` del compose.

La decisión de usar Docker Compose sigue siendo la referencia para nuevos entornos o
desarrollo local (donde el venv no está preinstalado). El procedimiento operativo
completo está en el [runbook del Analytics Engineer §3.2](../runbooks/analytics-engineer.md).
