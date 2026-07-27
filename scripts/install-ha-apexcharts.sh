#!/usr/bin/env bash
# Ensure Lovelace frontend cards used by the EMHASS trading dashboard.
# Idempotent: downloads only when missing or size looks wrong.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib/source-site-env.sh
source "${ROOT}/scripts/lib/source-site-env.sh"

HA_CONFIG_DIR="${HA_CONFIG_DIR:-/opt/homeassistant/config}"
HA_SSH="${HA_SSH_USER}@${HA_SSH_HOST}"

APEX_VERSION="${APEXCHARTS_CARD_VERSION:-v2.2.3}"
CTC_VERSION="${CONFIG_TEMPLATE_CARD_VERSION:-1.3.6}"

# Pipe into remote bash -s so a fish login shell cannot mis-parse the payload.
ssh -p "${HA_SSH_PORT}" "${HA_SSH}" bash -s -- \
  "${HA_CONFIG_DIR}" \
  "${APEX_VERSION}" \
  "${CTC_VERSION}" <<'REMOTE'
set -euo pipefail
HA_CONFIG_DIR="$1"
APEX_VERSION="$2"
CTC_VERSION="$3"

mkdir -p "${HA_CONFIG_DIR}/www"

ensure_js() {
  local path="$1"
  local url="$2"
  local need=1
  if [[ -f "${path}" ]]; then
    local size
    size="$(wc -c < "${path}")"
    if [[ "${size}" -gt 10000 ]]; then
      need=0
    fi
  fi
  if [[ "${need}" -eq 1 ]]; then
    curl -fsSL -o "${path}" "${url}"
  fi
  ls -la "${path}"
}

ensure_js \
  "${HA_CONFIG_DIR}/www/apexcharts-card.js" \
  "https://github.com/RomRider/apexcharts-card/releases/download/${APEX_VERSION}/apexcharts-card.js"

ensure_js \
  "${HA_CONFIG_DIR}/www/config-template-card.js" \
  "https://github.com/iantrich/config-template-card/releases/download/${CTC_VERSION}/config-template-card.js"

python3 - "${HA_CONFIG_DIR}" "${APEX_VERSION}" "${CTC_VERSION}" <<'PY'
import json
import sys
from pathlib import Path

ha_config_dir, apex_version, ctc_version = sys.argv[1:4]
apex_ver = apex_version.lstrip("v")
resources = [
    {
        "id": f"apexcharts_card_{apex_ver.replace('.', '')}",
        "url": f"/local/apexcharts-card.js?v={apex_ver}",
        "type": "module",
    },
    {
        "id": f"config_template_card_{ctc_version.replace('.', '').replace('-', '')}",
        "url": f"/local/config-template-card.js?v={ctc_version}",
        "type": "module",
    },
]
p = Path(ha_config_dir) / ".storage" / "lovelace_resources"
if p.exists():
    data = json.loads(p.read_text())
else:
    data = {
        "version": 1,
        "minor_version": 1,
        "key": "lovelace_resources",
        "data": {"items": []},
    }
items = data.setdefault("data", {}).setdefault("items", [])
keep_urls = {r["url"].split("?")[0] for r in resources}
items[:] = [
    i
    for i in items
    if not any(
        key in str(i.get("url", ""))
        for key in ("apexcharts-card", "config-template-card")
    )
]
items.extend(resources)
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(data, indent=2) + "\n")
for r in resources:
    print(f"resource: {r['url']}")
PY
REMOTE
