#!/usr/bin/env bash
# Push EMHASS + Sigen Home Assistant packages (rendered from config/site.env).
# Targets HA Container (Docker Compose), not HAOS Supervisor.
set -euo pipefail
unset CDPATH 2>/dev/null || true
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

WUD_ONLY=false
while (($# > 0)); do
  case "$1" in
    --wud-only)
      WUD_ONLY=true
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
  shift
done

# shellcheck source=lib/source-site-env.sh
source "${ROOT}/scripts/lib/source-site-env.sh"

HA_CONFIG_DIR="${HA_CONFIG_DIR:-/opt/homeassistant/config}"
HA_CONTAINER_NAME="${HA_CONTAINER_NAME:-homeassistant}"
HA_COMPOSE_DIR="${HA_COMPOSE_DIR:-/opt/homeassistant}"
HA_COMPOSE_PATH="${HA_COMPOSE_FILE:-${HA_COMPOSE_DIR}/docker-compose.yml}"
HA_COMPOSE_PARENT="$(dirname "${HA_COMPOSE_PATH}")"
HA_COMPOSE_ARGS=(-f "${HA_COMPOSE_PATH}")
HA_WUD_ENV_PATH="${HA_COMPOSE_PARENT}/wud.env"

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_PREFIX="${HA_DEPLOY_PREFIX:-energy-stack}"
HA_SSH="${HA_SSH_USER}@${HA_SSH_HOST}"
RENDERED="${ROOT}/.rendered/home-assistant"

PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON=python3
fi

remote() {
  ssh -p "${HA_SSH_PORT}" "${HA_SSH}" "$@"
}

remote_quote() {
  printf '%q' "$1"
}

remote_compose() {
  local argument
  local command=""
  for argument in docker compose "${HA_COMPOSE_ARGS[@]}" --env-file "${HA_WUD_ENV_PATH}" "$@"; do
    printf -v command '%s %q' "${command}" "${argument}"
  done
  remote "${command# }"
}

upload_to_path() {
  local source="$1"
  local destination="$2"
  remote "cat > $(remote_quote "${destination}")" < "${source}"
}

upload_wud_env() {
  local quoted_parent
  local quoted_destination
  local remote_script
  quoted_parent="$(remote_quote "${HA_COMPOSE_PARENT}")"
  quoted_destination="$(remote_quote "${HA_WUD_ENV_PATH}")"
  remote_script="set -e; umask 077; temporary_file=\$(mktemp ${quoted_parent}/.wud.env.XXXXXX); cleanup() { rm -f \"\$temporary_file\"; }; trap cleanup EXIT HUP INT TERM; cat > \"\$temporary_file\"; chmod 600 \"\$temporary_file\"; mv \"\$temporary_file\" ${quoted_destination}; trap - EXIT HUP INT TERM"
  remote "bash -c '${remote_script}'" < "${ROOT}/config/wud.env"
}

"${PYTHON}" "${ROOT}/scripts/lib/validate_wud_env.py" "${ROOT}/config/wud.env"

if [[ "${WUD_ONLY}" == true ]]; then
  HA_COMPOSE_BACKUP="${HA_CONFIG_DIR}/backups/${BACKUP_PREFIX}-${STAMP}/$(basename "${HA_COMPOSE_PATH}")"

  echo "Preflight: writable selected Compose parent and docker access on HA host..."
  remote "test -d $(remote_quote "${HA_COMPOSE_PARENT}") && test -w $(remote_quote "${HA_COMPOSE_PARENT}")" || {
    echo "HA Compose parent missing or not writable: ${HA_COMPOSE_PARENT}" >&2
    exit 1
  }
  remote "docker info >/dev/null" || {
    echo "docker not usable without sudo on ${HA_SSH} (add user to docker group)." >&2
    exit 1
  }

  remote "mkdir -p $(remote_quote "$(dirname "${HA_COMPOSE_BACKUP}")")"
  remote "cp $(remote_quote "${HA_COMPOSE_PATH}") $(remote_quote "${HA_COMPOSE_BACKUP}")"
  upload_to_path "${ROOT}/home-assistant/docker-compose.yml" "${HA_COMPOSE_PATH}"
  upload_wud_env

  remote_compose config --quiet
  remote_compose pull wud
  remote_compose up -d wud
  echo "WUD-only deploy complete. Backup: ${HA_COMPOSE_BACKUP}"
  exit 0
fi

if ! "$PYTHON" -c "import ruamel.yaml" 2>/dev/null; then
  "$PYTHON" -m pip install -q -r "${ROOT}/scripts/lib/requirements.txt"
fi

"${ROOT}/scripts/render-ha-packages.sh"

if [[ ! -d "${RENDERED}" ]]; then
  echo "Render output missing: ${RENDERED}" >&2
  exit 1
fi

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

# Import price forecast chart (custom:apexcharts-card) — ensure JS + Lovelace resource.
"${ROOT}/scripts/install-ha-apexcharts.sh"

upload() {
  upload_to_path "$1" "${HA_CONFIG_DIR}/$2"
}

for f in ha_emhass.yaml ha_emhass_sensors.yaml ha_emhass_dashboard.yaml ha_emhass_monitoring.yaml ha_emhass_helpers.yaml ha_sigen_ems.yaml ha_sigen_diagnostics.yaml; do
  upload "${RENDERED}/${f}" "${f}"
done
upload "${RENDERED}/dashboards/emhass_trading.yaml" "dashboards/emhass_trading.yaml"
upload "${RENDERED}/dashboards/sigen_diagnostics.yaml" "dashboards/sigen_diagnostics.yaml"

remote "cat '${HA_CONFIG_DIR}/configuration.yaml'" > "/tmp/ha_configuration_live.yaml"
PYTHONPATH="${ROOT}/scripts/lib" "$PYTHON" "${ROOT}/scripts/lib/merge_ha_config_cli.py" \
  --manifest energy-stack \
  --live "/tmp/ha_configuration_live.yaml" \
  --out "/tmp/ha_configuration_merged.yaml"
upload "/tmp/ha_configuration_merged.yaml" "configuration.yaml"

echo "Running Home Assistant config check (docker exec)..."
remote "docker exec '${HA_CONTAINER_NAME}' hass --script check_config -c /config"
upload_to_path "${ROOT}/home-assistant/docker-compose.yml" "${HA_COMPOSE_PATH}"
upload_wud_env
echo "Restarting Home Assistant container..."
remote_compose restart "${HA_CONTAINER_NAME}"
echo "Deploy complete. Backup: ${HA_CONFIG_DIR}/backups/${BACKUP_PREFIX}-${STAMP}/"
