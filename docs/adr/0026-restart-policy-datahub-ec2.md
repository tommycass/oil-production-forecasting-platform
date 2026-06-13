# Título: ADR-026: Política de reinicio de DataHub tras ciclo stop/start de la EC2 de gobierno

**Estado:** Aceptado

> Complementa [ADR-017](0017-plataforma-gobierno-datos.md), que decidió desplegar
> DataHub vía `datahub docker quickstart` en una EC2 dedicada (`t3.large`). Este ADR
> cubre **qué política de reinicio aplicar a los contenedores** para que DataHub
> sobreviva a un stop/start de instancia, y **cómo automatizar ese setup** para que
> no quede como un paso manual omisible.

---

## Contexto

`datahub docker quickstart` gestiona su propio `docker-compose` y levanta los
contenedores con la **política de reinicio por defecto de Docker: `no`**. Eso significa
que al **apagar la EC2 de gobierno y volver a prenderla** (escenario habitual para ahorrar
créditos), los 6 contenedores de larga duración quedan en estado `Exited` y DataHub no
arranca solo. El administrador debe entrar por SSM y correr `datahub docker quickstart`
manualmente cada vez — lo que es error-prone y bloquea cualquier demo o ingesta
automática post-pipeline.

Hay además un segundo problema conexo: si el daemon de Docker no está habilitado en el
boot del SO (`systemctl enable docker`), el propio motor de Docker no arranca al
encenderse la instancia, con lo que ninguna política de contenedor funciona de todas
formas.

### Qué contenedores existen en DataHub v1.5+ (KRaft, OpenSearch)

| Contenedor | Tipo | ¿Debe reiniciarse? |
|---|---|---|
| `datahub-mysql-1` | Almacén de metadata | Sí |
| `datahub-opensearch-1` | Motor de búsqueda | Sí |
| `datahub-kafka-broker-1` | Bus de eventos (KRaft, sin ZooKeeper) | Sí |
| `datahub-datahub-gms-quickstart-1` | Backend GMS (API) | Sí |
| `datahub-frontend-quickstart-1` | UI (puerto 9002) | Sí |
| `datahub-datahub-actions-quickstart-1` | Procesador de acciones asíncronas | Sí |
| `datahub-system-update` | Job de migraciones (Exited 0) | **No** — job one-shot; si se reinicia loop indefinidamente |

### Evaluación de políticas de reinicio

| Política | Comportamiento | Evaluación |
|---|---|---|
| `no` (defecto de quickstart) | Contenedores no reinician nunca de forma automática | ❌ DataHub no vuelve tras stop/start de EC2 |
| `always` | Reinicia siempre, incluyendo tras `docker stop` o `quickstart --stop` | ❌ Ignoraría paradas intencionales (mantenimiento, actualización de versión); imposibilita `docker stop` como mecanismo de pausa |
| `on-failure` | Solo si el contenedor salió con código distinto de 0 | ❌ No cubre stop/start de EC2: Docker para la instancia limpiamente (exit 0) |
| `unless-stopped` | Reinicia salvo que se lo haya parado intencionalmente con `docker stop` | ✅ Sobrevive al ciclo stop/start de la instancia; respeta las paradas manuales de mantenimiento |

### Por qué `unless-stopped` es la elección correcta

- **Cubre el caso de uso central:** stop/start de la EC2 → Docker arranca → los
  contenedores reanudan su ejecución anterior (no se crearon de nuevo, mantienen su
  estado).
- **Respeta las paradas de mantenimiento:** un `docker stop datahub-mysql-1` o
  `datahub docker quickstart --stop` marca los contenedores como "parados
  intencionalmente" → `unless-stopped` no los vuelve a levantar hasta que el operador
  corra `docker start` o `quickstart` de nuevo. Con `always` ese mecanismo no existe.
- **Compatible con actualizaciones:** para actualizar DataHub, el flujo es
  `quickstart --stop` → modificar/actualizar → `quickstart` de nuevo. Con `unless-stopped`
  ese ciclo no genera reinicios involuntarios.

### Por qué automatizar con un script en vez de solo documentar el paso manual

El setup de `docker update --restart unless-stopped` es un paso **post-deploy** que:
- No forma parte del `quickstart` ni de su compose.
- No se puede configurar antes de que los contenedores existan.
- Es fácil de omitir si solo está en texto en un runbook, especialmente durante un
  despliegue bajo presión.

Un script en el repositorio (`infra/governance/setup-datahub.sh`) resuelve esto:
es reproducible, está versionado, se puede relanzar idempotentemente si algo falla a
mitad de camino, y sirve tanto para el despliegue inicial como para onboarding de un
administrador nuevo. Además, espera activamente a que el GMS esté healthy antes de
aplicar la política, evitando que se configure sobre contenedores que todavía están
arrancando.

---

## Decisión

1. **Política `unless-stopped`** en los 6 contenedores de larga duración de DataHub
   (excluir `datahub-system-update`, que es un job one-shot y no debe reiniciarse):

   ```bash
   docker update --restart unless-stopped \
     datahub-mysql-1 \
     datahub-opensearch-1 \
     datahub-kafka-broker-1 \
     datahub-datahub-gms-quickstart-1 \
     datahub-frontend-quickstart-1 \
     datahub-datahub-actions-quickstart-1
   ```

2. **`systemctl enable docker`** para asegurar que el daemon de Docker arranque con el
   SO. Sin esto, incluso `unless-stopped` no funciona porque Docker no llega a correr.

3. **Script `infra/governance/setup-datahub.sh`** que encapsula todo el setup
   (instalar CLI, `quickstart`, esperar GMS healthy, aplicar políticas, habilitar Docker
   en boot) en un único comando idempotente. Es la **fuente de verdad del proceso de
   deploy**, no un complemento opcional del runbook.

4. **Regla operativa:** para apagar la EC2 de gobierno, hacerlo siempre desde la
   **consola/CLI de AWS** (EC2 stop). **No correr** `datahub docker quickstart --stop`
   ni `docker stop` antes de apagar: eso marca los contenedores como "parados
   intencionalmente" y `unless-stopped` —por diseño— no los reanuda en el siguiente boot.

---

## Consecuencias

**Positivas:**
- DataHub está disponible automáticamente ~3-4 minutos después de que la EC2 prende,
  sin intervención manual. Los reinicios transitorios del GMS mientras OpenSearch y Kafka
  terminan de levantar son esperados y se estabiliza solo.
- El setup completo queda en un script versionado y auditable en el repo, no en notas
  personales o pasos de runbook que se saltan.
- La política `unless-stopped` preserva la capacidad de hacer mantenimiento sin luchar
  contra reinicios automáticos del propio motor.
- Idempotente: se puede relanzar `setup-datahub.sh` si el deploy falla a mitad de camino.

**Negativas:**
- La regla operativa (no usar `docker stop` antes de apagar la EC2) debe internalizarse;
  si se viola, los contenedores no arrancan solos en el siguiente boot y hay que
  relanzar `quickstart` manualmente.
- El script asume que los nombres de contenedor siguen el patrón `datahub-*-1` del
  quickstart oficial. Si DataHub cambia su esquema de nombres en una versión futura,
  el `docker update` puede fallar silenciosamente; el script tiene un fallback que
  itera sobre todos los contenedores con `--filter name=datahub` y excluye `system-update`.

## Relación con otros ADRs

- **ADR-017:** toma la decisión de plataforma (DataHub) y el modo de deploy
  (`quickstart`); este ADR cierra el gap operativo que esa decisión dejó abierto.
- **ADR-023:** la UI de Dagster corre vía compose local, no en la EC2 de gobierno,
  así que no está afectada por esta política.
