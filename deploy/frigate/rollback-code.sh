#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /opt/frigate/deploy-backups/YYYYMMDD-HHMMSS" >&2
  exit 1
fi

BACKUP_DIR="$1"
REMOTE_ROOT="/opt/frigate/custom-build/frigate"
REMOTE_MIGRATIONS="/opt/frigate/custom-build/migrations"
REMOTE_COMPOSE="/opt/frigate/docker-compose.yml"
REMOTE_PREFLIGHT="/opt/frigate/storage-preflight.py"

ssh pve4 "pct exec 240 -- bash -lc 'set -euo pipefail; test -d \"${BACKUP_DIR}\"; test -f \"${BACKUP_DIR}/docker-compose.yml\"; cp \"${BACKUP_DIR}/docker-compose.yml\" \"${REMOTE_COMPOSE}\"; if [[ -f \"${BACKUP_DIR}/storage-preflight.py\" ]]; then cp \"${BACKUP_DIR}/storage-preflight.py\" \"${REMOTE_PREFLIGHT}\"; chmod 0755 \"${REMOTE_PREFLIGHT}\"; fi; if [[ -f \"${BACKUP_DIR}/frigate__api__media.py\" ]]; then cp \"${BACKUP_DIR}/frigate__api__media.py\" \"${REMOTE_ROOT}/api/media.py\"; fi; if [[ -f \"${BACKUP_DIR}/frigate__tier_migrator.py\" ]]; then cp \"${BACKUP_DIR}/frigate__tier_migrator.py\" \"${REMOTE_ROOT}/tier_migrator.py\"; fi; if [[ -f \"${BACKUP_DIR}/migrations__034_add_recordings_tier_migration_index.py\" ]]; then mkdir -p \"${REMOTE_MIGRATIONS}\"; cp \"${BACKUP_DIR}/migrations__034_add_recordings_tier_migration_index.py\" \"${REMOTE_MIGRATIONS}/034_add_recordings_tier_migration_index.py\"; fi; cd /opt/frigate; if docker compose version >/dev/null 2>&1; then docker compose config -q && docker compose up -d --force-recreate frigate frigate-tier-migrator; else docker-compose config -q && docker-compose up -d --force-recreate frigate frigate-tier-migrator; fi'"
ssh pve4 "pct exec 240 -- bash -lc 'for i in \$(seq 1 30); do health=\$(docker inspect frigate --format \"{{.State.Health.Status}}\" 2>/dev/null || true); if [[ \"\$health\" == healthy ]] && curl -sf http://127.0.0.1:5000/api/config >/dev/null; then docker ps --filter name=frigate --format \"{{.Names}} {{.Status}}\"; exit 0; fi; sleep 2; done; docker logs --tail 80 frigate; exit 1'"
ssh pve4 "pct exec 240 -- bash -lc 'for i in \$(seq 1 40); do health=\$(docker inspect frigate-tier-migrator --format \"{{.State.Health.Status}}\" 2>/dev/null || true); if [[ \"\$health\" == healthy ]]; then docker ps --filter name=frigate-tier-migrator --format \"{{.Names}} {{.Status}}\"; exit 0; fi; sleep 3; done; docker logs --tail 120 frigate-tier-migrator; exit 1'"
ssh pve4 "pct exec 240 -- bash -lc 'cat >/opt/frigate/deploy-state.env <<EOF
REPO_URL='git@github.com:forgedcold/frigate.git'
BRANCH='custom/v0.17.1-tiered-storage'
DEPLOYED_SHA='rollback-unknown'
DEPLOYED_AT='rollback'
EOF
systemctl start frigate-deploy-drift-check.service 2>/dev/null || true'"

echo
echo "Rolled back CT240 code deployment from ${BACKUP_DIR}"
