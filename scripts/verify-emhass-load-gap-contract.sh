#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON}" ]]; then
  PYTHON=python3
fi

compose_image="$(
  "${PYTHON}" - "${ROOT}/docker-compose.yml" <<'PY'
import sys
from pathlib import Path
from ruamel.yaml import YAML

compose_path = Path(sys.argv[1])
with compose_path.open(encoding="utf-8") as stream:
    compose = YAML(typ="safe").load(stream)
print(compose["services"]["emhass"]["image"])
PY
)"
image="${1:-${compose_image}}"

if [[ ! "${image}" =~ :[^@[:space:]]+@sha256:[0-9a-f]{64}$ ]]; then
  echo "Candidate must use repository:release@sha256:digest: ${image}" >&2
  exit 2
fi
if [[ "${image}" == *":latest@"* ]]; then
  echo "Candidate must not use :latest: ${image}" >&2
  exit 2
fi

docker pull "${image}" >/dev/null
docker run --rm \
  --entrypoint python3 \
  --volume "${ROOT}/scripts/emhass_load_gap_contract.py:/contract.py:ro" \
  "${image}" \
  /contract.py
