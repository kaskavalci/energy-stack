#!/usr/bin/env bash
# Push EMHASS + Sigen Home Assistant packages (rendered from config/site.env).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib/source-site-env.sh
source "${ROOT}/scripts/lib/source-site-env.sh"

HA="/mnt/data/supervisor/homeassistant"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_PREFIX="${HA_DEPLOY_PREFIX:-energy-stack}"
HA_SSH="${HA_SSH_USER}@${HA_SSH_HOST}"
RENDERED="${ROOT}/.rendered/home-assistant"

PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON=python3
fi
if ! "$PYTHON" -c "import ruamel.yaml" 2>/dev/null; then
  "$PYTHON" -m pip install -q -r "${ROOT}/scripts/lib/requirements.txt"
fi

"${ROOT}/scripts/render-ha-packages.sh"

if [[ ! -d "${RENDERED}" ]]; then
  echo "Render output missing: ${RENDERED}" >&2
  exit 1
fi

echo "Preflight: emhass_publish_url in HA secrets.yaml..."
if ! ssh -p "${HA_SSH_PORT}" "${HA_SSH}" "grep -q '^emhass_publish_url:' ${HA}/secrets.yaml 2>/dev/null"; then
  echo "Missing emhass_publish_url in ${HA}/secrets.yaml on Home Assistant." >&2
  echo "Add the key from docs/ha-secrets.example.yaml — see docs/SETUP.md" >&2
  exit 1
fi

ssh -p "${HA_SSH_PORT}" "${HA_SSH}" "mkdir -p ${HA}/backups/${BACKUP_PREFIX}-${STAMP} ${HA}/dashboards"
ssh -p "${HA_SSH_PORT}" "${HA_SSH}" "cp ${HA}/configuration.yaml ${HA}/backups/${BACKUP_PREFIX}-${STAMP}/"

upload() {
  ssh -p "${HA_SSH_PORT}" "${HA_SSH}" "cat > ${HA}/$2" < "$1"
}

for f in ha_emhass.yaml ha_emhass_sensors.yaml ha_emhass_dashboard.yaml ha_emhass_monitoring.yaml ha_emhass_helpers.yaml ha_sigen_ems.yaml; do
  upload "${RENDERED}/${f}" "${f}"
done
upload "${RENDERED}/dashboards/emhass_trading.yaml" "dashboards/emhass_trading.yaml"

ssh -p "${HA_SSH_PORT}" "${HA_SSH}" "cat ${HA}/configuration.yaml" > "/tmp/ha_configuration_live.yaml"
PYTHONPATH="${ROOT}/scripts/lib" "$PYTHON" "${ROOT}/scripts/lib/merge_ha_config_cli.py" \
  --manifest energy-stack \
  --live "/tmp/ha_configuration_live.yaml" \
  --out "/tmp/ha_configuration_merged.yaml"
upload "/tmp/ha_configuration_merged.yaml" "configuration.yaml"

echo "Running ha core check..."
ssh -p "${HA_SSH_PORT}" "${HA_SSH}" "ha core check"
echo "Restarting Home Assistant core..."
ssh -p "${HA_SSH_PORT}" "${HA_SSH}" "ha core restart"
echo "Deploy complete. Backup: ${HA}/backups/${BACKUP_PREFIX}-${STAMP}/"
