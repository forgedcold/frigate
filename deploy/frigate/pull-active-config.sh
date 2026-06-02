#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_CONFIG="${SCRIPT_DIR}/config.stock-ab.yml"
REMOTE_TMP="/tmp/config.stock-ab.yml.frigate-repo-sync"
LOCAL_TMP="/tmp/config.stock-ab.yml.frigate-repo-sync"

ssh pve4 "pct pull 240 /opt/frigate/config/config.stock-ab.yml ${REMOTE_TMP}" >/dev/null
scp "pve4:${REMOTE_TMP}" "${LOCAL_TMP}" >/dev/null
cp "${LOCAL_TMP}" "${REPO_CONFIG}"

echo "Pulled active CT240 config into ${REPO_CONFIG}"
echo
echo "Review this diff before committing:"
git -C "$(cd "${SCRIPT_DIR}/../.." && pwd)" diff -- deploy/frigate/config.stock-ab.yml
