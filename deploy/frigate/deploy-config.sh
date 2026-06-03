#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_CONFIG="${SCRIPT_DIR}/config.stock-ab.yml"
REMOTE_TMP="/tmp/config.stock-ab.yml.frigate-repo-deploy"
ACTIVE_CONFIG="/opt/frigate/config/config.stock-ab.yml"
BACKUP_CONFIG="${ACTIVE_CONFIG}.bak-$(date +%Y%m%d-%H%M%S)"

if [[ ! -f "${REPO_CONFIG}" ]]; then
  echo "Missing repo config: ${REPO_CONFIG}" >&2
  exit 1
fi

python3 - <<PY
import pathlib
import sys
import yaml

config_path = pathlib.Path("${REPO_CONFIG}")
try:
    with config_path.open() as f:
        yaml.safe_load(f)
except Exception as exc:
    print(f"Invalid YAML in {config_path}: {exc}", file=sys.stderr)
    sys.exit(1)
PY

mapfile -t placeholders < <(grep -o "{FRIGATE_[A-Z0-9_]*}" "${REPO_CONFIG}" | tr -d "{}" | sort -u)
if (( ${#placeholders[@]} > 0 )); then
  missing=()
  env_output="$(ssh pve4 "pct exec 240 -- bash -lc 'docker exec frigate env'")"
  for name in "${placeholders[@]}"; do
    if ! grep -q "^${name}=" <<<"${env_output}"; then
      missing+=("${name}")
    fi
  done

  if (( ${#missing[@]} > 0 )); then
    echo "CT240 Frigate container is missing required config placeholders:" >&2
    printf '  %s\n' "${missing[@]}" >&2
    echo "Add these environment variables before deploying this config." >&2
    exit 1
  fi
fi

scp "${REPO_CONFIG}" "pve4:${REMOTE_TMP}" >/dev/null

ssh pve4 "pct exec 240 -- bash -lc 'set -euo pipefail; cp ${ACTIVE_CONFIG} ${BACKUP_CONFIG}'"
ssh pve4 "pct push 240 ${REMOTE_TMP} ${ACTIVE_CONFIG}" >/dev/null
ssh pve4 "pct exec 240 -- bash -lc 'docker restart frigate >/dev/null'"
ssh pve4 "pct exec 240 -- bash -lc 'for i in \$(seq 1 30); do curl -sf http://127.0.0.1:5000/api/config >/dev/null && docker ps --filter name=frigate --format \"{{.Names}} {{.Status}}\" && exit 0; sleep 2; done; docker logs --tail 80 frigate; exit 1'"

echo
echo "Deployed ${REPO_CONFIG}"
echo "Backup: ${BACKUP_CONFIG}"
