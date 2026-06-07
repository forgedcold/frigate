#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
HOST_TMP="/tmp/frigate-code-deploy-$$"
REMOTE_ROOT="/opt/frigate/custom-build/frigate"
REMOTE_MIGRATIONS="/opt/frigate/custom-build/migrations"
REMOTE_COMPOSE="/opt/frigate/docker-compose.yml"
REMOTE_DEPLOY_STATE="/opt/frigate/deploy-state.env"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"
DETECTOR_SCRIPT="${SCRIPT_DIR}/frigate-deploy-drift-check"
DETECTOR_SERVICE="${SCRIPT_DIR}/frigate-deploy-drift-check.service"
DETECTOR_TIMER="${SCRIPT_DIR}/frigate-deploy-drift-check.timer"

declare -a FILES=(
  "frigate/api/media.py"
  "frigate/tier_migrator.py"
  "migrations/034_add_recordings_tier_migration_index.py"
)

if [[ ! -f "${COMPOSE_FILE}" ]]; then
  echo "Missing compose file: ${COMPOSE_FILE}" >&2
  exit 1
fi

for required in "${DETECTOR_SCRIPT}" "${DETECTOR_SERVICE}" "${DETECTOR_TIMER}"; do
  if [[ ! -f "${required}" ]]; then
    echo "Missing deploy drift detector file: ${required}" >&2
    exit 1
  fi
done

for rel in "${FILES[@]}"; do
  src="${REPO_ROOT}/${rel}"
  if [[ ! -f "${src}" ]]; then
    echo "Missing source file: ${src}" >&2
    exit 1
  fi

  if [[ "${src}" == *.py ]]; then
    python3 - <<PY
import ast
from pathlib import Path
ast.parse(Path("${src}").read_text())
PY
  fi
done

ssh pve4 "mkdir -p ${HOST_TMP}/files"
mkdir -p "${HOST_TMP}"
scp "${COMPOSE_FILE}" "pve4:${HOST_TMP}/docker-compose.yml" >/dev/null
scp "${DETECTOR_SCRIPT}" "pve4:${HOST_TMP}/frigate-deploy-drift-check" >/dev/null
scp "${DETECTOR_SERVICE}" "pve4:${HOST_TMP}/frigate-deploy-drift-check.service" >/dev/null
scp "${DETECTOR_TIMER}" "pve4:${HOST_TMP}/frigate-deploy-drift-check.timer" >/dev/null

for rel in "${FILES[@]}"; do
  src="${REPO_ROOT}/${rel}"
  tmp_name="${rel//\//__}"
  scp "${src}" "pve4:${HOST_TMP}/files/${tmp_name}" >/dev/null
done

timestamp="$(date +%Y%m%d-%H%M%S)"
deployed_sha="$(git -C "${REPO_ROOT}" rev-parse HEAD)"
deployed_branch="$(git -C "${REPO_ROOT}" rev-parse --abbrev-ref HEAD)"
cat >"${HOST_TMP}/deploy-state.env" <<EOF
REPO_URL='git@github.com:forgedcold/frigate.git'
BRANCH='${deployed_branch}'
DEPLOYED_SHA='${deployed_sha}'
DEPLOYED_AT='${timestamp}'
EOF
scp "${HOST_TMP}/deploy-state.env" "pve4:${HOST_TMP}/deploy-state.env" >/dev/null

ssh pve4 "pct exec 240 -- bash -lc 'set -euo pipefail; mkdir -p /opt/frigate/deploy-backups/${timestamp}'"
ssh pve4 "pct exec 240 -- bash -lc 'set -euo pipefail; cp ${REMOTE_COMPOSE} /opt/frigate/deploy-backups/${timestamp}/docker-compose.yml'"

for rel in "${FILES[@]}"; do
  tmp_name="${rel//\//__}"
  if [[ "${rel}" == frigate/* ]]; then
    remote_dest="${REMOTE_ROOT}/${rel#frigate/}"
  elif [[ "${rel}" == migrations/* ]]; then
    remote_dest="${REMOTE_MIGRATIONS}/${rel#migrations/}"
  else
    echo "Unsupported deploy file path: ${rel}" >&2
    exit 1
  fi
  ssh pve4 "pct exec 240 -- bash -lc 'set -euo pipefail; mkdir -p \"$(dirname "${remote_dest}")\"; if [[ -f \"${remote_dest}\" ]]; then cp \"${remote_dest}\" \"/opt/frigate/deploy-backups/${timestamp}/${tmp_name}\"; fi'"
  ssh pve4 "pct push 240 ${HOST_TMP}/files/${tmp_name} ${remote_dest}" >/dev/null
done

ssh pve4 "pct push 240 ${HOST_TMP}/docker-compose.yml ${REMOTE_COMPOSE}" >/dev/null
ssh pve4 "pct push 240 ${HOST_TMP}/frigate-deploy-drift-check /usr/local/sbin/frigate-deploy-drift-check" >/dev/null
ssh pve4 "pct push 240 ${HOST_TMP}/frigate-deploy-drift-check.service /etc/systemd/system/frigate-deploy-drift-check.service" >/dev/null
ssh pve4 "pct push 240 ${HOST_TMP}/frigate-deploy-drift-check.timer /etc/systemd/system/frigate-deploy-drift-check.timer" >/dev/null
ssh pve4 "pct exec 240 -- bash -lc 'chmod 0755 /usr/local/sbin/frigate-deploy-drift-check; systemctl daemon-reload; systemctl enable --now frigate-deploy-drift-check.timer >/dev/null'"
ssh pve4 "pct exec 240 -- bash -lc 'set -euo pipefail; cd /opt/frigate; if docker compose version >/dev/null 2>&1; then docker compose config -q && docker compose up -d --force-recreate frigate frigate-tier-migrator; else docker-compose config -q && docker-compose up -d --force-recreate frigate frigate-tier-migrator; fi'"
ssh pve4 "pct exec 240 -- bash -lc 'for i in \$(seq 1 30); do health=\$(docker inspect frigate --format \"{{.State.Health.Status}}\" 2>/dev/null || true); if [[ \"\$health\" == healthy ]] && curl -sf http://127.0.0.1:5000/api/config >/dev/null; then docker ps --filter name=frigate --format \"{{.Names}} {{.Status}}\"; exit 0; fi; sleep 2; done; docker logs --tail 80 frigate; exit 1'"
ssh pve4 "pct exec 240 -- bash -lc 'for i in \$(seq 1 40); do health=\$(docker inspect frigate-tier-migrator --format \"{{.State.Health.Status}}\" 2>/dev/null || true); if [[ \"\$health\" == healthy ]]; then docker ps --filter name=frigate-tier-migrator --format \"{{.Names}} {{.Status}}\"; exit 0; fi; sleep 3; done; docker logs --tail 120 frigate-tier-migrator; exit 1'"

ssh pve4 "pct exec 240 -- bash -lc 'now=\$(date +%s); start=\$((now-240)); end=\$((now-120)); curl -sf \"http://127.0.0.1:5000/vod/duo3_front/start/\$start/end/\$end/master.m3u8\" | tee /tmp/duo3-vod-check.m3u8; grep -q \"RESOLUTION=1536x432\" /tmp/duo3-vod-check.m3u8'"
ssh pve4 "pct push 240 ${HOST_TMP}/deploy-state.env ${REMOTE_DEPLOY_STATE}" >/dev/null
ssh pve4 "pct exec 240 -- bash -lc 'systemctl start frigate-deploy-drift-check.service; cat /opt/frigate/config/deploy_drift_status.json'"
ssh pve4 "rm -rf ${HOST_TMP}"
rm -rf "${HOST_TMP}"

echo
echo "Deployed code files to CT240."
echo "Backup directory: /opt/frigate/deploy-backups/${timestamp}"
