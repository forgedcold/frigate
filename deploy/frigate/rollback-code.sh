#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /opt/frigate/deploy-backups/YYYYMMDD-HHMMSS" >&2
  exit 1
fi

BACKUP_DIR="$1"
REMOTE_ROOT="/opt/frigate/custom-build/frigate"
REMOTE_COMPOSE="/opt/frigate/docker-compose.yml"

ssh pve4 "pct exec 240 -- bash -lc 'set -euo pipefail; test -d \"${BACKUP_DIR}\"; test -f \"${BACKUP_DIR}/docker-compose.yml\"; cp \"${BACKUP_DIR}/docker-compose.yml\" \"${REMOTE_COMPOSE}\"; if [[ -f \"${BACKUP_DIR}/frigate__api__media.py\" ]]; then cp \"${BACKUP_DIR}/frigate__api__media.py\" \"${REMOTE_ROOT}/api/media.py\"; fi; cd /opt/frigate; if docker compose version >/dev/null 2>&1; then docker compose config -q && docker compose up -d --force-recreate frigate; else docker-compose config -q && docker-compose up -d --force-recreate frigate; fi'"
ssh pve4 "pct exec 240 -- bash -lc 'for i in \$(seq 1 30); do health=\$(docker inspect frigate --format \"{{.State.Health.Status}}\" 2>/dev/null || true); if [[ \"\$health\" == healthy ]] && curl -sf http://127.0.0.1:5000/api/config >/dev/null; then docker ps --filter name=frigate --format \"{{.Names}} {{.Status}}\"; exit 0; fi; sleep 2; done; docker logs --tail 80 frigate; exit 1'"

echo
echo "Rolled back CT240 code deployment from ${BACKUP_DIR}"
