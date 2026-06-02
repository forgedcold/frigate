#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_CONFIG="${SCRIPT_DIR}/config.stock-ab.yml"
REMOTE_TMP="/tmp/config.stock-ab.yml.frigate-repo-diff"
LOCAL_TMP="$(mktemp /tmp/frigate-active-config.XXXXXX.yml)"

cleanup() {
  rm -f "${LOCAL_TMP}"
}
trap cleanup EXIT

ssh pve4 "pct pull 240 /opt/frigate/config/config.stock-ab.yml ${REMOTE_TMP}" >/dev/null
scp "pve4:${REMOTE_TMP}" "${LOCAL_TMP}" >/dev/null

diff -u "${LOCAL_TMP}" "${REPO_CONFIG}" || true
