#!/usr/bin/env bash
# Push energy-stack from this repo to the energy host (source of truth: laptop).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib/source-site-env.sh
source "${ROOT}/scripts/lib/source-site-env.sh"

PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON}" ]]; then
  PYTHON=python3
fi

echo "Validating EMHASS site location..."
"${PYTHON}" "${ROOT}/scripts/lib/validate_site_location.py"

REMOTE_HOST="${ENERGY_REMOTE_HOST}"
REMOTE_DIR="${ENERGY_REMOTE_DIR}"

rsync -avz \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '/.worktrees/' \
  --exclude '/.superpowers/' \
  --exclude '/.rendered/' \
  --exclude '/.bug-hunter/' \
  --exclude '/.pytest_cache/' \
  --exclude '/.cursorrules' \
  --exclude 'home-assistant' \
  --exclude 'scripts' \
  --exclude 'docs' \
  --exclude 'memory-bank' \
  --exclude '.agents' \
  --exclude '.cursor' \
  --exclude 'AGENTS.md' \
  --exclude 'README.md' \
  --exclude 'configuration.yaml' \
  --exclude 'emhass/share/data_*_forecast.csv' \
  --exclude 'emhass/share/prices_fetched_at.txt' \
  --exclude '/emhass/share/last_enever_run.json' \
  --exclude 'emhass/share/config.json.bak*' \
  --exclude '__pycache__' \
  "${ROOT}/" "${REMOTE_HOST}:${REMOTE_DIR}/"

ssh "${REMOTE_HOST}" "rm -f ${REMOTE_DIR}/crontab ${REMOTE_DIR}/Dockerfile ${REMOTE_DIR}/fetch_prices_emhass.py ${REMOTE_DIR}/README.md 2>/dev/null || true"

echo "Building and restarting emhass, emhass-cron, energy-monitor..."
ssh "${REMOTE_HOST}" "cd ${REMOTE_DIR} && docker compose build emhass-cron energy-monitor && docker compose up -d --force-recreate emhass && docker compose up -d emhass-cron energy-monitor"

echo "Done. Verify: docker compose exec emhass-cron python3 /app/fetch_prices_emhass.py --action mpc -v"
