#!/usr/bin/env bash
# Push EMHASS + Sigen Home Assistant packages (rendered from config/site.env).
# Targets HA Container (Docker Compose), not HAOS Supervisor.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib/source-site-env.sh
source "${ROOT}/scripts/lib/source-site-env.sh"

HA_CONFIG_DIR="${HA_CONFIG_DIR:-/opt/homeassistant/config}"
HA_CONTAINER_NAME="${HA_CONTAINER_NAME:-homeassistant}"
HA_COMPOSE_DIR="${HA_COMPOSE_DIR:-/opt/homeassistant}"
if [[ -n "${HA_COMPOSE_FILE:-}" ]]; then
  HA_COMPOSE_ARGS=(-f "${HA_COMPOSE_FILE}")
else
  HA_COMPOSE_ARGS=(-f "${HA_COMPOSE_DIR}/docker-compose.yml")
fi

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

remote() {
  ssh -p "${HA_SSH_PORT}" "${HA_SSH}" "$@"
}

echo "Preflight: writable config dir and docker access on HA host..."
remote "test -d '${HA_CONFIG_DIR}' && test -w '${HA_CONFIG_DIR}'" || {
  echo "HA config dir missing or not writable: ${HA_CONFIG_DIR}" >&2
  echo "Create it owned by the deploy user (no sudo at deploy time)." >&2
  exit 1
}
remote "docker info >/dev/null" || {
  echo "docker not usable without sudo on ${HA_SSH} (add user to docker group)." >&2
  exit 1
}

echo "Preflight: emhass_publish_url in HA secrets.yaml..."
if ! remote "grep -q '^emhass_publish_url:' '${HA_CONFIG_DIR}/secrets.yaml' 2>/dev/null"; then
  echo "Missing emhass_publish_url in ${HA_CONFIG_DIR}/secrets.yaml on Home Assistant." >&2
  echo "Add the key from docs/ha-secrets.example.yaml — see docs/SETUP.md" >&2
  exit 1
fi

remote "mkdir -p '${HA_CONFIG_DIR}/backups/${BACKUP_PREFIX}-${STAMP}' '${HA_CONFIG_DIR}/dashboards'"
remote "cp '${HA_CONFIG_DIR}/configuration.yaml' '${HA_CONFIG_DIR}/backups/${BACKUP_PREFIX}-${STAMP}/'"

upload() {
  remote "cat > '${HA_CONFIG_DIR}/$2'" < "$1"
}

for f in ha_emhass.yaml ha_emhass_sensors.yaml ha_emhass_dashboard.yaml ha_emhass_monitoring.yaml ha_emhass_helpers.yaml ha_sigen_ems.yaml; do
  upload "${RENDERED}/${f}" "${f}"
done
upload "${RENDERED}/dashboards/emhass_trading.yaml" "dashboards/emhass_trading.yaml"

remote "cat '${HA_CONFIG_DIR}/configuration.yaml'" > "/tmp/ha_configuration_live.yaml"
PYTHONPATH="${ROOT}/scripts/lib" "$PYTHON" "${ROOT}/scripts/lib/merge_ha_config_cli.py" \
  --manifest energy-stack \
  --live "/tmp/ha_configuration_live.yaml" \
  --out "/tmp/ha_configuration_merged.yaml"
upload "/tmp/ha_configuration_merged.yaml" "configuration.yaml"

echo "Running Home Assistant config check (docker exec)..."
remote "docker exec '${HA_CONTAINER_NAME}' hass --script check_config -c /config"
echo "Restarting Home Assistant container..."
remote "docker compose ${HA_COMPOSE_ARGS[*]} restart '${HA_CONTAINER_NAME}'"
echo "Deploy complete. Backup: ${HA_CONFIG_DIR}/backups/${BACKUP_PREFIX}-${STAMP}/"
