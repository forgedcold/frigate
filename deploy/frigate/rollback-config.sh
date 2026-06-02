#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /opt/frigate/config/config.stock-ab.yml.bak-YYYYMMDD-HHMMSS" >&2
  exit 1
fi

BACKUP_CONFIG="$1"
ACTIVE_CONFIG="/opt/frigate/config/config.stock-ab.yml"

ssh pve4 "pct exec 240 -- bash -lc 'set -euo pipefail; test -f ${BACKUP_CONFIG}; cp ${BACKUP_CONFIG} ${ACTIVE_CONFIG}; docker restart frigate >/dev/null; curl -sf http://127.0.0.1:5000/api/config >/dev/null; docker ps --filter name=frigate --format \"{{.Names}} {{.Status}}\"'"

echo
echo "Rolled back CT240 Frigate config from ${BACKUP_CONFIG}"
