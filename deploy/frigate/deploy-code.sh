#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
HOST_TMP="/tmp/frigate-code-deploy-$$"
REMOTE_ROOT="/opt/frigate/custom-build/frigate"
REMOTE_COMPOSE="/opt/frigate/docker-compose.yml"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"

declare -a FILES=(
  "frigate/api/media.py"
)

if [[ ! -f "${COMPOSE_FILE}" ]]; then
  echo "Missing compose file: ${COMPOSE_FILE}" >&2
  exit 1
fi

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
scp "${COMPOSE_FILE}" "pve4:${HOST_TMP}/docker-compose.yml" >/dev/null

for rel in "${FILES[@]}"; do
  src="${REPO_ROOT}/${rel}"
  tmp_name="${rel//\//__}"
  scp "${src}" "pve4:${HOST_TMP}/files/${tmp_name}" >/dev/null
done

timestamp="$(date +%Y%m%d-%H%M%S)"

ssh pve4 "pct exec 240 -- bash -lc 'set -euo pipefail; mkdir -p /opt/frigate/deploy-backups/${timestamp}'"
ssh pve4 "pct exec 240 -- bash -lc 'set -euo pipefail; cp ${REMOTE_COMPOSE} /opt/frigate/deploy-backups/${timestamp}/docker-compose.yml'"

for rel in "${FILES[@]}"; do
  tmp_name="${rel//\//__}"
  remote_dest="${REMOTE_ROOT}/${rel#frigate/}"
  ssh pve4 "pct exec 240 -- bash -lc 'set -euo pipefail; mkdir -p \"$(dirname "${remote_dest}")\"; if [[ -f \"${remote_dest}\" ]]; then cp \"${remote_dest}\" \"/opt/frigate/deploy-backups/${timestamp}/${tmp_name}\"; fi'"
  ssh pve4 "pct push 240 ${HOST_TMP}/files/${tmp_name} ${remote_dest}" >/dev/null
done

ssh pve4 "pct push 240 ${HOST_TMP}/docker-compose.yml ${REMOTE_COMPOSE}" >/dev/null
ssh pve4 "pct exec 240 -- bash -lc 'set -euo pipefail; cd /opt/frigate; if docker compose version >/dev/null 2>&1; then docker compose config -q && docker compose up -d --force-recreate frigate; else docker-compose config -q && docker-compose up -d --force-recreate frigate; fi'"
ssh pve4 "pct exec 240 -- bash -lc 'for i in \$(seq 1 30); do health=\$(docker inspect frigate --format \"{{.State.Health.Status}}\" 2>/dev/null || true); if [[ \"\$health\" == healthy ]] && curl -sf http://127.0.0.1:5000/api/config >/dev/null; then docker ps --filter name=frigate --format \"{{.Names}} {{.Status}}\"; exit 0; fi; sleep 2; done; docker logs --tail 80 frigate; exit 1'"

ssh pve4 "pct exec 240 -- bash -lc 'now=\$(date +%s); start=\$((now-240)); end=\$((now-120)); curl -sf \"http://127.0.0.1:5000/vod/duo3_front/start/\$start/end/\$end/master.m3u8\" | tee /tmp/duo3-vod-check.m3u8; grep -q \"RESOLUTION=1536x432\" /tmp/duo3-vod-check.m3u8'"
ssh pve4 "rm -rf ${HOST_TMP}"

echo
echo "Deployed code files to CT240."
echo "Backup directory: /opt/frigate/deploy-backups/${timestamp}"
