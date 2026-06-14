#!/usr/bin/env bash
# setup-datahub.sh — Setup completo de DataHub en la EC2 de gobierno.
#
# Uso (como ubuntu en la EC2 de gobierno):
#   chmod +x setup-datahub.sh
#   ./setup-datahub.sh
#
# Qué hace:
#   1. Verifica que Docker esté instalado y corriendo.
#   2. Instala el CLI de DataHub (Python 3.11, extra [datahub-rest]).
#   3. Levanta el stack de DataHub vía quickstart (~3 GB de imágenes, 5-10 min).
#   4. Espera hasta que el GMS esté healthy (hasta 5 min).
#   5. Aplica restart policy `unless-stopped` a los 6 servicios de larga duración.
#   6. Habilita Docker en el arranque del sistema operativo (systemctl enable).
#
# Después de este script:
#   - Apagar la EC2 desde AWS Console/CLI → al prenderla, DataHub arranca solo.
#   - NO correr `datahub docker quickstart --stop` ni `docker stop` antes de apagar.
#
# Idempotente: se puede correr de nuevo si algo falla; el quickstart detecta
# contenedores existentes y no los recrea (solo arranca los que están parados).
set -euo pipefail

DATAHUB_GMS_PORT="${DATAHUB_GMS_PORT:-8080}"
DATAHUB_GMS_URL="http://localhost:${DATAHUB_GMS_PORT}"
MAX_WAIT_SECONDS=300   # 5 min para que el GMS quede healthy
PYTHON="${PYTHON:-python3}"
DATAHUB_VENV="${DATAHUB_VENV:-$HOME/.datahub-venv}"
DATAHUB="$DATAHUB_VENV/bin/datahub"

# Contenedores de larga duración de DataHub v1.5+ (KRaft, OpenSearch, sin ZooKeeper).
# datahub-system-update corre migraciones y sale con Exited 0 — no se incluye.
DATAHUB_SERVICES=(
  "datahub-mysql-1"
  "datahub-opensearch-1"
  "datahub-kafka-broker-1"
  "datahub-datahub-gms-quickstart-1"
  "datahub-frontend-quickstart-1"
  "datahub-datahub-actions-quickstart-1"
)

log() { echo "[setup-datahub] $(date '+%H:%M:%S') $*"; }

# 1. Verificar Docker
log "Verificando Docker..."
if ! command -v docker >/dev/null 2>&1; then
  log "ERROR: Docker no está instalado. Instalarlo primero:"
  log "  sudo apt-get update && sudo apt-get install -y docker.io"
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  log "El daemon de Docker no está corriendo. Iniciándolo..."
  sudo systemctl start docker
fi
log "Docker OK: $(docker --version)"

# 2. Instalar DataHub CLI en virtualenv (Ubuntu 24.04 bloquea pip install global — PEP 668)
log "Creando virtualenv en $DATAHUB_VENV e instalando DataHub CLI..."
$PYTHON -m venv "$DATAHUB_VENV"
"$DATAHUB_VENV/bin/pip" install --quiet --upgrade pip
"$DATAHUB_VENV/bin/pip" install --quiet --upgrade 'acryl-datahub[datahub-rest]'
log "DataHub CLI: $($DATAHUB version 2>/dev/null || echo 'instalado')"

# Agregar el venv al PATH permanentemente para uso manual posterior
PROFILE_LINE="export PATH=\"$DATAHUB_VENV/bin:\$PATH\""
if ! grep -qF "$DATAHUB_VENV/bin" "$HOME/.bashrc" 2>/dev/null; then
  echo "$PROFILE_LINE" >> "$HOME/.bashrc"
  log "PATH actualizado en ~/.bashrc (activá con: source ~/.bashrc)"
fi

# 3. Levantar quickstart
log "Lanzando datahub docker quickstart (puede tardar 5-10 min en el primer arranque)..."
$DATAHUB docker quickstart

# 4. Esperar a que el GMS esté healthy
log "Esperando que el GMS esté disponible en ${DATAHUB_GMS_URL}..."
ELAPSED=0
until curl -sf "${DATAHUB_GMS_URL}/health" >/dev/null 2>&1; do
  if [ "$ELAPSED" -ge "$MAX_WAIT_SECONDS" ]; then
    log "ERROR: GMS no respondió después de ${MAX_WAIT_SECONDS}s."
    log "Revisar: docker ps --filter name=datahub && docker logs datahub-datahub-gms-quickstart-1"
    exit 1
  fi
  sleep 10
  ELAPSED=$((ELAPSED + 10))
  log "  ... ${ELAPSED}s esperados (GMS aún no responde)"
done
log "GMS OK (respondió en ${ELAPSED}s)"

# 5. Aplicar restart policy unless-stopped
log "Aplicando restart policy 'unless-stopped' a los servicios de larga duración..."
MISSING=()
for svc in "${DATAHUB_SERVICES[@]}"; do
  if docker inspect "$svc" >/dev/null 2>&1; then
    docker update --restart unless-stopped "$svc"
    log "  ✓ $svc"
  else
    MISSING+=("$svc")
    log "  ! $svc no encontrado (puede que el nombre sea distinto en esta versión)"
  fi
done

# Si faltan contenedores, intentar con los que realmente existen
if [ "${#MISSING[@]}" -gt 0 ]; then
  log "Aplicando unless-stopped a todos los contenedores datahub existentes..."
  docker ps -aq --filter name=datahub | while read -r cid; do
    name=$(docker inspect --format '{{.Name}}' "$cid" | tr -d '/')
    # Excluir el job de migraciones (termina con Exited 0 y no debe reiniciarse)
    if echo "$name" | grep -q "system-update"; then
      log "  - $name omitido (job de migraciones, no es un servicio)"
    else
      docker update --restart unless-stopped "$cid"
      log "  ✓ $name"
    fi
  done
fi

# 6. Habilitar Docker en el boot
log "Habilitando Docker para que arranque con el sistema..."
sudo systemctl enable docker
log "Docker habilitado en el boot del sistema."

# Verificación final
log "=== Verificación final ==="
docker inspect -f '{{.Name}}: {{.HostConfig.RestartPolicy.Name}}' \
  $(docker ps -aq --filter name=datahub) 2>/dev/null || true

log ""
log "=== Setup completo ==="
log "  DataHub UI:  http://$(curl -sf http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo '<ip-governance>'):9002"
log "  DataHub GMS: http://$(curl -sf http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo '<ip-governance>'):8080"
log "  Usuario/pass: datahub / datahub"
log ""
log "  IMPORTANTE: apagá la instancia desde AWS Console (stop de EC2)."
log "  NO corras 'datahub docker quickstart --stop' ni 'docker stop' antes de apagar."
log "  Al volver a prenderla, DataHub arranca solo (~3-4 min de estabilización)."
