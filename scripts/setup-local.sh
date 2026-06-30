#!/usr/bin/env bash
# First-time local setup: copy examples, render HA packages and sigenergy config.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cp -n "${ROOT}/config/site.env.example" "${ROOT}/config/site.env" 2>/dev/null || true
cp -n "${ROOT}/emhass/secrets_emhass.yaml.example" "${ROOT}/emhass/secrets_emhass.yaml" 2>/dev/null || true
cp -n "${ROOT}/emhass/share/config.json.example" "${ROOT}/emhass/share/config.json" 2>/dev/null || true

mkdir -p "${ROOT}/sigenergy" "${ROOT}/mosquitto/config"
if [[ ! -f "${ROOT}/sigenergy/sigenergy2mqtt.yaml" ]]; then
  cp "${ROOT}/sigenergy/sigenergy2mqtt.yaml.example" "${ROOT}/sigenergy/sigenergy2mqtt.yaml"
fi
if [[ ! -f "${ROOT}/mosquitto/config/mosquitto.conf" ]]; then
  cp "${ROOT}/mosquitto/config/mosquitto.conf.example" "${ROOT}/mosquitto/config/mosquitto.conf"
fi

# shellcheck source=lib/source-site-env.sh
source "${ROOT}/scripts/lib/source-site-env.sh"
render_sigenergy() {
  local src="${ROOT}/sigenergy/sigenergy2mqtt.yaml.example"
  local dest="${ROOT}/sigenergy/sigenergy2mqtt.yaml"
  local content
  content="$(<"$src")"
  content="${content//__SIGENERGY_MODBUS_HOST__/${SIGENERGY_MODBUS_HOST}}"
  content="${content//__SIGENERGY_MODBUS_SLAVE__/${SIGENERGY_MODBUS_SLAVE}}"
  printf '%s' "$content" > "$dest"
}
render_sigenergy

"${ROOT}/scripts/render-ha-packages.sh"

cat <<EOF

Setup complete. Next steps:
  1. Edit config/site.env (hosts, entity IDs, Modbus IP)
  2. Edit emhass/secrets_emhass.yaml (hass_url, long_lived_token)
  3. Add emhass_publish_url to Home Assistant secrets.yaml (docs/ha-secrets.example.yaml)
  4. Re-run: ./scripts/render-ha-packages.sh  (after site.env changes)
  5. Deploy: ./scripts/deploy-energy-stack.sh && ./scripts/deploy-home-assistant.sh

See docs/SETUP.md
EOF
