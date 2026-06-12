# Runbook — Analytics Engineer

**Procedimiento:** Reconstruir las capas Silver/Gold del Data Warehouse y resolver
el gate de Data Quality (incluido el caso en que un check crítico bloquea la
promoción a Gold).

Rol de perfil de implementación. Complementa los [ADR-014](../adr/0014-arquitectura-medallion.md),
[ADR-015](../adr/0015-modelo-dimensional-estrella.md), [ADR-016](../adr/0016-estrategia-data-quality.md)
y el [modelo de datos](../data-model.md).

---

## 1. Propósito y disparador

Materializar Silver y Gold a partir de Bronze y dejar el modelo estrella listo y
**confiable** para BI (Metabase), gobierno (DataHub) y la API. Se ejecuta cuando:

- **Backfill / nueva ingesta de Bronze:** el Data Engineer reprocesó un mes (corrección
  vía `rectificado`) o cargó datos nuevos en Bronze. Hay que propagar a Gold.
- **Cambio de modelo:** se modificó un modelo dbt (nueva dimensión, métrica, regla
  de limpieza) y hay que publicarlo.
- **Incidente de calidad:** un check `severity: error` falló y **bloqueó la promoción a
  Gold** — la corrida del cron termina en error (visible en `cron.log` y en `dq.dq_results`)
  o lo reporta el administrador de BI/gobierno porque un dashboard quedó sin datos frescos. (La alerta push a Slack
  ante el fallo es evolución pendiente, ver ADR-016.)

## 2. Rol / dueño y prerrequisitos

- **Dueño:** Analytics Engineer.
- **Accesos:** credenciales del DW Postgres (`POSTGRES_*`), repo, y permiso de
  ejecución del proyecto `transform/`.
- **Herramientas:** Python 3.11, dbt-core + dbt-postgres, paquetes dbt
  (`dbt deps`). El container `postgres` del `docker-compose` arriba (o el endpoint
  Postgres gestionado en AWS).
- **Insumo:** capa Bronze poblada en el esquema `bronze` (la deja A vía
  `load_bronze.py`, o `seed_sample_bronze.py` para una muestra de prueba).

### Provisión inicial del DW (una vez por entorno)

Las bases del DW (`oil_dw_staging`, `oil_dw_prod`) deben existir antes de correr el
pipeline. Crearlas es idempotente con `infra/db/bootstrap.sql` (usa `\gexec`; ver el
header del archivo). dbt y `load_bronze.py` crean los **schemas** solos
(`bronze/silver/gold/dq`), no hace falta crearlos a mano.

```bash
# Conecta a la base de mantenimiento `postgres`; el secreto va por el entorno.
psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d postgres \
     -v ON_ERROR_STOP=1 -f infra/db/bootstrap.sql
```

En AWS (RDS) el cliente psql se puede correr vía Docker desde la EC2 con acceso a RDS:

```bash
sudo docker run --rm -i -e PGPASSWORD="$POSTGRES_PASSWORD" postgres:16 \
  psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d postgres \
       -v ON_ERROR_STOP=1 < infra/db/bootstrap.sql
```

## 3. Pasos

```bash
# 1. Posicionarse y activar entorno
cd transform
source venv/bin/activate            # o crear venv + pip install -r requirements.txt
export POSTGRES_HOST=localhost POSTGRES_PORT=5432 \
       POSTGRES_USER=oil POSTGRES_PASSWORD=oil POSTGRES_DB=oil_dw

# 1b. (Recomendado en checkouts compartidos / root-owned, p.ej. la EC2 de staging)
#     Escribir los artefactos efímeros de dbt FUERA del repo, en un dir del usuario.
#     Así el ownership del repo (el deploy hace `git pull` como root) no rompe dbt
#     con PermissionError al crear logs/ o target/. En local/CI dejá estas vars sin
#     setear: dbt usa los defaults dentro del proyecto.
export DBT_LOG_PATH="$HOME/dbt-runtime/logs"
export DBT_TARGET_PATH="$HOME/dbt-runtime/target"
#     Nota: target/ contiene manifest.json (lo consumen `dbt docs`, las corridas con
#     `state:` y el lineage de dbt a DataHub). Si lo relocalizás, esos consumidores
#     deben apuntar al mismo DBT_TARGET_PATH.

# 2. Asegurar paquetes dbt
dbt deps

# 3. Confirmar que Bronze tiene los datos esperados (sanity del insumo del Data Engineer)
dbt source freshness --profiles-dir .

# 4. Construir Silver + Gold y correr Data Quality en orden de dependencia
dbt build --profiles-dir .
#    - Materializa silver_* → corre checks de Silver → si pasan, materializa gold_* → checks de Gold.
#    - Si un check `error` de Silver falla, Gold queda SKIPPED (promoción bloqueada).

# 5. (opcional) Regenerar documentación/linaje para gobierno
dbt docs generate --profiles-dir .
```

## 3.1 Orquestación end-to-end en AWS (Dagster + cron)

Los pasos de §3 son la corrida manual de dbt. En AWS el flujo corre **completo y
automatizado**: un grafo de Dagster (`data_pipeline/orchestration/`) materializa
`Bronze(parquet) → Bronze(Postgres) → Silver/Gold/DQ` y lo dispara el cron. Así BI
(tablas `gold.*`) y gobierno (manifest de dbt) consumen sin pasos manuales. Es
**env-driven**: el mismo procedimiento sirve a staging (`oil_dw_staging`) y prod
(`oil_dw_prod`); lo único que cambia es el `infra/.env` de cada EC2.

> El grafo extiende la zona de orquestación del Data Engineer — ver "Actualización (jun-2026)" en
> ADR-011 (revisada y aceptada por el Data Engineer; decisión formal en ADR-018).

### a) Setup (una vez por EC2)

```bash
cd /home/ubuntu/oil-production-forecasting-platform
# El deploy hace `git pull` como root → asegurar ownership de ubuntu:
sudo chown -R ubuntu:ubuntu /home/ubuntu/oil-production-forecasting-platform

# Memoria: los pasos pandas (landing 144 MB, load_bronze) pueden necesitar RAM.
# Opción A (recomendada): agrandar la instancia. Opción B (red de seguridad): swap.
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile \
  && sudo mkswap /swapfile && sudo swapon /swapfile      # persistir: agregar a /etc/fstab

# Venv ÚNICO para Dagster, FUERA del repo (no lo pisa el deploy/chown):
python3 -m venv ~/dagster-venv
source ~/dagster-venv/bin/activate
pip install -r data_pipeline/requirements.txt -r transform/requirements.txt

# Manifest de dbt (lo consume dagster-dbt) + paquetes dbt:
set -a; source infra/.env; set +a        # exporta POSTGRES_* (incl. POSTGRES_DB del entorno)
cd transform && dbt deps && dbt parse --profiles-dir . && cd ..
```

### b) Backfill histórico (una vez, "carga real")

Puebla todas las particiones de Bronze y construye el DW. La fuente no convencional
no arranca en 2006; se toma el rango real del landing ya bajado.

```bash
source ~/dagster-venv/bin/activate
set -a; source infra/.env; set +a
MOD=data_pipeline.orchestration.definitions

dagster asset materialize -m $MOD --select produccion_raw   # landing (1 vez)
dagster asset materialize -m $MOD --select bronze_pozos

# Rango real de años en la fuente:
python - <<'PY'
import pandas as pd
from data_pipeline.extraction.extract_produccion import _LANDING_FILE
a = pd.read_parquet(_LANDING_FILE, columns=["anio"])["anio"].astype(int)
print("RANGO", a.min(), a.max())
PY

# Materializar cada mes del rango (meses sin datos escriben parquet vacío, inocuo):
for y in $(seq <MIN> <MAX>); do for m in $(seq 1 12); do
  dagster asset materialize -m $MOD --select bronze_produccion --partition "$(printf '%04d-%02d-01' $y $m)"
done; done

dagster job execute -m $MOD -j dw_publish   # Bronze→Postgres + dbt (Silver/Gold/DQ)
```

### c) Refresh recurrente (cron)

`run_pipeline.sh` recarga **todo** Bronze (full reload: atrapa altas y correcciones de
cualquier mes vía `rectificado`, ver ADR-021) + `dw_publish`. La fuente es mensual, así
que un cron mensual alcanza:

```bash
mkdir -p ~/dagster-runtime
crontab -l 2>/dev/null | { cat; echo "0 3 5 * * /home/ubuntu/oil-production-forecasting-platform/data_pipeline/orchestration/run_pipeline.sh >> /home/ubuntu/dagster-runtime/cron.log 2>&1"; } | crontab -
```

### d) Rollout a producción

Repetir (a)–(c) en la EC2 de prod. **Único cambio:** su `infra/.env` tiene
`POSTGRES_DB=oil_dw_prod`. El código y los comandos son idénticos.

## 4. Validación (cómo sé que salió bien)

- La corrida termina en `Completed successfully` con `ERROR=0` y `SKIP=0`.
- El último lote de checks en `dq.dq_results` está todo en `pass`:

```sql
select dimension, status, count(*)
from dq.dq_results
where invocation_id = (select invocation_id from dq.dq_results order by executed_at desc limit 1)
group by dimension, status order by dimension;
```

- Control de grano de la fact (debe dar 0 duplicados):

```sql
select count(*) from (
  select sk_pozo, sk_fecha from gold.fact_produccion_mensual
  group by 1,2 having count(*) > 1
) d;
```

- Conteo razonable de filas en `gold.fact_produccion_mensual` y dimensiones sin
  crecer de golpe respecto a la corrida anterior (no se duplicó por un backfill mal hecho).

## 5. Si algo falla

- **Un check `error` bloqueó Gold (caso más común):**
  1. Identificar el test en el log o en `dq.dq_results` (status `fail`).
  2. Inspeccionar las filas ofensoras en su tabla de `store_failures`, p. ej.
     `select * from dq.dbt_utils_unique_combination_o_...;` o la tabla del test.
  3. Si es **dato sucio de la fuente** (duplicado por `rectificado`, valor fuera de
     rango): coordinar con **A** el reprocesamiento del mes en Bronze; **no** relajar
     el check para "destrabar". Re-correr `dbt build` tras el fix.
  4. Si es un **falso positivo / regla mal calibrada**: ajustar el test (umbral,
     severidad) en el `.yml`, justificarlo en el PR y re-correr.
- **Rollback:** Gold no se reconstruye si Silver falla, así que **el Gold anterior
  sigue intacto y servido** (BI/API no consumen datos rotos). Para volver a un modelo
  previo: `git revert` del commit del modelo y `dbt build`. Como las capas son
  full-refresh determinísticas, reconstruir reproduce el estado exacto.
- **Plan B:** si el DW no está disponible, BI puede seguir leyendo el último Gold
  materializado; se pausa la promoción hasta restablecer Postgres.
- **Escalamiento:** problema en Bronze/extracción → Data Engineer; problema de
  conexión/infra del Postgres en AWS → administrador de infra/gobierno; lineage o BI sin datos → administrador de gobierno.

## 6. Consideraciones no funcionales

- **Frescura:** la promoción a Gold solo es tan fresca como Bronze; el check de
  freshness (`warn`) avisa si la última ingesta supera el umbral. SLA de frescura a
  acordar con el Data Engineer según cadencia del DAG.
- **Calidad:** los checks `error` (unicidad de PK, no-nulos de claves, producción no
  negativa, integridad referencial) son el contrato de confiabilidad de Gold.
- **Costo / latencia:** el `dbt build` full-refresh corre en segundos a la escala
  actual; bajo costo, prioriza determinismo.
- **Seguridad / PII:** las fuentes son datos públicos de producción de hidrocarburos
  (sin PII); el riesgo de privacidad es bajo. Igual, las credenciales del DW van por
  variables de entorno, nunca en el repo.
- **Gobernanza:** cada corrida deja audit-trail en `dq.dq_results` y linaje navegable
  en DataHub; los cambios de modelo pasan por PR con review.

## 7. Decisiones del proyecto que este rol ownea

### Decisión funcional — qué checks bloquean vs solo avisan

**Decisión:** marco como **bloqueantes (`severity: error`)** la unicidad de la clave
de negocio (`idpozo+anio+mes`), los no-nulos de claves, la producción no negativa y la
integridad referencial fact→dim; el resto (variantes de nombres, outliers leves,
freshness) queda como **`warn`** (registra, no bloquea).

**Por qué, desde mis incentivos como Analytics Engineer:** Gold es *mi* entregable y
de él salen todas las sumas de los dashboards de negocio y los números de la API. Si
un `idpozo+anio+mes` se duplicara o se colara una producción negativa, **toda
agregación quedaría mal sin que nadie lo note** hasta que un usuario de negocio
desconfíe del tablero — y la credibilidad del modelo (y mía) se quema ahí. Prefiero
*frenar* la promoción y arreglar el dato en origen antes que servir un Gold que suma
mal. En cambio, una variante de nombre de operadora ("YPF S.A." vs "YPF SA") molesta
pero no corrompe los totales: avisa, no bloquea, para no parar todo el pipeline por
algo cosmético. La línea entre "bloquea" y "avisa" la trazo según **qué error
contamina los números que consume negocio**, que es exactamente lo que se me pide
garantizar.

### Decisión no funcional — materializar Silver/Gold como `table` (full refresh)

**Decisión:** materializo Silver y Gold como `table` reconstruidas en cada corrida,
en lugar de modelos `incremental`.

**Por qué, desde mis incentivos como Analytics Engineer:** a la escala actual el
full-refresh corre en segundos, así que el ahorro de cómputo de lo incremental es
marginal, pero su costo escondido no lo es: el estado incremental **deriva**
(filas que llegaron tarde, un mes corregido por `rectificado`, un cambio de lógica que
no se re-aplica a lo viejo) y termina en bugs sutiles de "por qué este número no
coincide" que me toca depurar a mí. El full-refresh me da **idempotencia y
reprocesabilidad** gratis: re-correr produce exactamente el mismo Gold, que es justo
lo que la consigna exige y lo que me evita perseguir discrepancias. Cambio un poco de
cómputo barato por reproducibilidad y noches tranquilas; si el volumen creciera al
punto de doler, recién ahí evaluaría incremental en la fact de producción.
