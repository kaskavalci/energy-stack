#!/usr/bin/env bash
# Render home-assistant/*.yaml templates using config/site.env __PLACEHOLDER__ tokens.
set -euo pipefail
unset CDPATH 2>/dev/null || true
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib/source-site-env.sh
source "${ROOT}/scripts/lib/source-site-env.sh"

OUT="${ROOT}/.rendered/home-assistant"
STAMP="${ROOT}/.rendered/render-stamp"

export ENVOY_PV_ENTITY SIGEN_PLANT_SLUG P1_POWER_NET_ENTITY P1_POWER_CONSUMPTION_ENTITY P1_POWER_PRODUCTION_ENTITY

render_file() {
  local src="$1"
  local dest="$2"
  local content
  content="$(<"$src")"
  content="${content//__ENVOY_PV_ENTITY__/${ENVOY_PV_ENTITY}}"
  content="${content//__SIGEN_PLANT_SLUG__/${SIGEN_PLANT_SLUG}}"
  content="${content//__P1_POWER_NET_ENTITY__/${P1_POWER_NET_ENTITY}}"
  content="${content//__P1_POWER_CONSUMPTION_ENTITY__/${P1_POWER_CONSUMPTION_ENTITY}}"
  content="${content//__P1_POWER_PRODUCTION_ENTITY__/${P1_POWER_PRODUCTION_ENTITY}}"
  printf '%s' "$content" > "$dest"
}

mkdir -p "${OUT}/dashboards"

for f in "${ROOT}"/home-assistant/*.yaml; do
  [[ -f "$f" ]] || continue
  render_file "$f" "${OUT}/$(basename "$f")"
done

for f in "${ROOT}"/home-assistant/dashboards/*.yaml; do
  [[ -f "$f" ]] || continue
  render_file "$f" "${OUT}/dashboards/$(basename "$f")"
done

if command -v shasum >/dev/null 2>&1; then
  shasum -a 256 "${ROOT}/config/site.env" | awk '{print $1}' > "${STAMP}"
elif command -v sha256sum >/dev/null 2>&1; then
  sha256sum "${ROOT}/config/site.env" | awk '{print $1}' > "${STAMP}"
else
  stat -f '%m' "${ROOT}/config/site.env" > "${STAMP}" 2>/dev/null || stat -c '%Y' "${ROOT}/config/site.env" > "${STAMP}"
fi

echo "Rendered HA packages -> ${OUT}"
