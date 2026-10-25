#!/usr/bin/env bash
# Source config/site.env for deploy and render scripts.
# Usage: source "$(dirname "$0")/lib/source-site-env.sh"
set -euo pipefail
unset CDPATH 2>/dev/null || true

_SOURCE_SITE_ENV_ROOT="${SOURCE_SITE_ENV_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
_SITE_ENV="${_SOURCE_SITE_ENV_ROOT}/config/site.env"

if [[ ! -f "${_SITE_ENV}" ]]; then
  echo "Missing ${_SITE_ENV}" >&2
  echo "Run: ./scripts/setup-local.sh  (or: cp config/site.env.example config/site.env)" >&2
  return 1 2>/dev/null || exit 1
fi

# shellcheck disable=SC1090
set -a
source "${_SITE_ENV}"
set +a

# Defaults when keys omitted from site.env
export ENERGY_REMOTE_HOST="${ENERGY_REMOTE_HOST:-user@192.168.1.50}"
export ENERGY_REMOTE_DIR="${ENERGY_REMOTE_DIR:-~/workspace/energy-stack}"
export HA_SSH_USER="${HA_SSH_USER:-root}"
export HA_SSH_HOST="${HA_SSH_HOST:-192.168.1.51}"
export HA_SSH_PORT="${HA_SSH_PORT:-22222}"
