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

WUD_ENV="${ROOT}/config/wud.env"
echo "Validating WUD environment..."
"${PYTHON}" "${ROOT}/scripts/lib/validate_wud_env.py" "${WUD_ENV}"

REMOTE_HOST="${ENERGY_REMOTE_HOST}"
REMOTE_DIR="${ENERGY_REMOTE_DIR}"
if [[ "${REMOTE_DIR}" == "~/"* ]]; then
  REMOTE_DIR_REMOTE="~/$(printf '%q' "${REMOTE_DIR:2}")"
else
  REMOTE_DIR_REMOTE="$(printf '%q' "${REMOTE_DIR}")"
fi

rsync -avz \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '/.worktrees/' \
  --exclude '/.superpowers/' \
  --exclude '/.rendered/' \
  --exclude '/.bug-hunter/' \
  --exclude '/.pytest_cache/' \
  --exclude '/.cursorrules' \
  --exclude '/skills-lock.json' \
  --exclude '/tmp_emhass_now.py' \
  --exclude 'home-assistant' \
  --exclude 'scripts' \
  --exclude 'docs' \
  --exclude 'memory-bank' \
  --exclude '.agents' \
  --exclude '.cursor' \
  --exclude 'AGENTS.md' \
  --exclude 'README.md' \
  --exclude 'configuration.yaml' \
  --exclude 'config/wud.env' \
  --exclude 'emhass/share/data_*_forecast.csv' \
  --exclude 'emhass/share/prices_fetched_at.txt' \
  --exclude '/emhass/share/last_enever_run.json' \
  --exclude 'emhass/share/config.json.bak*' \
  --exclude '__pycache__' \
  "${ROOT}/" "${REMOTE_HOST}:${REMOTE_DIR}/"

ssh "${REMOTE_HOST}" "remote_dir=${REMOTE_DIR_REMOTE}; umask 077 && temporary_file=\$(mktemp \"\${remote_dir}/config/.wud.env.XXXXXX\") && trap 'rm -f \"\${temporary_file}\"' EXIT HUP INT TERM && cat > \"\${temporary_file}\" && chmod 600 \"\${temporary_file}\" && mv \"\${temporary_file}\" \"\${remote_dir}/config/wud.env\"" < "${WUD_ENV}"

ssh "${REMOTE_HOST}" "remote_dir=${REMOTE_DIR_REMOTE}; rm -f \"\${remote_dir}/crontab\" \"\${remote_dir}/Dockerfile\" \"\${remote_dir}/fetch_prices_emhass.py\" \"\${remote_dir}/README.md\" 2>/dev/null || true"

echo "Building and restarting emhass, emhass-cron, energy-monitor..."
ssh "${REMOTE_HOST}" "remote_dir=${REMOTE_DIR_REMOTE}; cd \"\${remote_dir}\" && docker compose --env-file config/wud.env config --quiet && docker compose --env-file config/wud.env pull wud && docker compose --env-file config/wud.env build emhass-cron energy-monitor && docker compose --env-file config/wud.env up -d --force-recreate emhass && docker compose --env-file config/wud.env up -d emhass-cron energy-monitor wud"

echo "Done. Verify: docker compose exec emhass-cron python3 /app/fetch_prices_emhass.py --action mpc -v"
