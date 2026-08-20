#!/usr/bin/env bash
# Load config/site.env, then deploy energy host + Home Assistant.
# Safe to run from fish: ./scripts/deploy-all.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib/source-site-env.sh
source "${ROOT}/scripts/lib/source-site-env.sh"

echo "==> Energy host (${ENERGY_REMOTE_HOST})"
"${ROOT}/scripts/deploy-energy-stack.sh"

echo "==> Home Assistant (${HA_SSH_USER}@${HA_SSH_HOST})"
"${ROOT}/scripts/deploy-home-assistant.sh"

echo "Deploy complete."
