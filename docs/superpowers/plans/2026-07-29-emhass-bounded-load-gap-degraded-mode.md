# EMHASS Bounded Load-Gap Degraded Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resume native EMHASS MPC automatically after fresh P1, PV, and
battery-derived house-load samples return from a fully bracketed outage of at
most six 30-minute bins, while retaining fail-closed behavior for longer or
ambiguous gaps.

**Architecture:** Pin the currently deployed EMHASS 0.17.5 image and prove its
native interpolation/naive-forecast behavior with a secret-free compatibility
probe. Extend the existing UTC load-history validator to classify history as
`healthy`, `degraded`, or `unsafe`; pass the classification through the
existing native MPC path and status file without submitting a runtime load
list. Make the monitor incident-aware so one degraded/unsafe gap produces one
primary notice and one recovery notice.

**Tech Stack:** Python 3.12 stdlib and `unittest`, pandas/numpy inside the
EMHASS 0.17.5 container, Docker Compose, Home Assistant template YAML, JSON
status files, shell deployment scripts.

## Global Constraints

- Read `memory-bank/activeContext.md` and `memory-bank/techContext.md` before
  implementation.
- Read `.agents/skills/emhass-energy-stack/SKILL.md` before live work.
- Source `config/site.env` before SSH, deployment, or live diagnostics. Never
  hardcode hosts, usernames, entity IDs, tokens, or device serials.
- Preserve unrelated worktree changes. In particular,
  `memory-bank/activeContext.md` already has an uncommitted incident update.
- Create implementation commits only when the user explicitly authorizes
  commits. The commit steps below are conditional on that authorization;
  otherwise leave the task diff unstaged and report the suggested message.
- Keep `optimization_time_step` at exactly 30 minutes.
- Keep `prediction_horizon` at 32 maximum and the price-slot minimum at 12.
- Allow exactly one interior run of one through six missing 30-minute bins.
- Seven bins, multiple gaps, edge gaps, stale newest samples, and missing
  brackets remain fail-closed.
- Keep the newest composite load-sample limit at five minutes and the existing
  HA source-template freshness limit at 20 minutes.
- Keep valid `0 W` history and `set_zero_min: false`.
- Do not write synthetic states into Home Assistant recorder history.
- Do not submit `load_power_forecast`, `alpha`, or `beta` from cron.
- Keep price methods `csv`; price-fetch failure still aborts MPC with no stale
  CSV fallback.
- Keep weather `open-meteo`,
  `open_meteo_cache_max_age: 120`, and
  `clear_weather_cache_files()` before a forced retry.
- Keep Home Assistant stale-plan/control gates and Sigenergy native
  self-consumption behavior unchanged.
- Do not make Zonneplan statistics a runtime load fallback.
- Pin EMHASS to
  `ghcr.io/davidusb-geek/emhass:v0.17.5@sha256:0aa7c9062ed269c138b6099bb2b6d7428f640d5c00cdbf31b7e0067345779b8e`.
  The digest currently resolves to EMHASS 0.17.5, source revision
  `a482de7c9e7aa43691fabc1910026168d60f5050`.
- Never change the EMHASS tag or digest until the compatibility probe, cron
  tests, and Compose pin test pass against the candidate.
- Exercise missing-history geometry only with synthetic fixtures. Never delete
  or alter live HA recorder data to test this feature.

## File Map

| File | Responsibility |
|---|---|
| `docker-compose.yml` | Pin the verified EMHASS release and immutable digest. |
| `scripts/emhass_load_gap_contract.py` | Probe installed EMHASS interpolation and native naive forecast using synthetic data. |
| `scripts/verify-emhass-load-gap-contract.sh` | Run the compatibility probe in a candidate pinned image without site secrets. |
| `scripts/tests/test_emhass_image_pin.py` | Reject `latest`, missing tags, missing digests, and a wrapper not bound to Compose. |
| `docs/EMHASS_UPGRADES.md` | Define the mandatory agent/operator upgrade procedure. |
| `docs/verification/emhass/v0.17.5-load-gap-contract.md` | Record the initial pinned-image compatibility evidence. |
| `AGENTS.md` | Make the compatibility check mandatory for future agents. |
| `emhass-cron/fetch_prices_emhass.py` | Classify history, carry diagnostics, and preserve native MPC behavior. |
| `emhass-cron/tests/test_fetch_prices_emhass.py` | Cover gap geometry, status propagation, dry-run, and causal failures. |
| `scripts/tests/test_ha_emhass_sensors.py` | Lock the composite sensor to fresh P1, PV, and battery inputs. |
| `monitor/health_notify.py` | Detect gap transitions, deduplicate incidents, and suppress derivative alerts. |
| `monitor/tests/test_health_notify.py` | Cover state migration, incident transitions, suppression, and retry behavior. |
| `emhass-cron/README.md` | Document healthy/degraded/unsafe runtime behavior and diagnostics. |
| `emhass/README.md` | Document the pinned upstream contract and native interpolation boundary. |
| `memory-bank/techContext.md` | Replace the absolute no-gap invariant with the approved bounded policy. |
| `memory-bank/activeContext.md` | Record actual local/deploy verification without overwriting the existing incident note. |

---

### Task 1: Pin EMHASS and add the upgrade compatibility contract

**Files:**

- Modify: `docker-compose.yml`
- Create: `scripts/emhass_load_gap_contract.py`
- Create: `scripts/verify-emhass-load-gap-contract.sh`
- Create: `scripts/tests/test_emhass_image_pin.py`
- Create: `docs/EMHASS_UPGRADES.md`
- Create: `docs/verification/emhass/v0.17.5-load-gap-contract.md`
- Modify: `AGENTS.md`

**Interfaces:**

- Consumes: a candidate image reference in
  `repository:release@sha256:digest` form.
- Produces:
  `scripts/verify-emhass-load-gap-contract.sh [IMAGE_REFERENCE]`, exit `0`
  with `EMHASS_LOAD_GAP_CONTRACT_OK` only when installed EMHASS preserves the
  verified interpolation and naive-forecast contract.
- Produces: an immutable Compose image reference used by deployment.

- [ ] **Step 1: Write the failing Compose-pin and wrapper tests**

Create `scripts/tests/test_emhass_image_pin.py`:

```python
from __future__ import annotations

import re
import unittest
from pathlib import Path

from ruamel.yaml import YAML


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"
WRAPPER = REPO_ROOT / "scripts" / "verify-emhass-load-gap-contract.sh"
PINNED_IMAGE = (
    "ghcr.io/davidusb-geek/emhass:v0.17.5@sha256:"
    "0aa7c9062ed269c138b6099bb2b6d7428f640d5c00cdbf31b7e0067345779b8e"
)
PIN_PATTERN = re.compile(
    r"^ghcr\.io/davidusb-geek/emhass:(?!latest(?:@|$))[^@\s]+"
    r"@sha256:[0-9a-f]{64}$"
)


class EmhassImagePinTests(unittest.TestCase):
    def test_compose_uses_verified_release_and_digest(self) -> None:
        with COMPOSE.open(encoding="utf-8") as stream:
            compose = YAML(typ="safe").load(stream)
        image = compose["services"]["emhass"]["image"]
        self.assertEqual(image, PINNED_IMAGE)
        self.assertRegex(image, PIN_PATTERN)

    def test_contract_wrapper_defaults_to_compose_image(self) -> None:
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("docker-compose.yml", text)
        self.assertIn(
            'compose["services"]["emhass"]["image"]',
            text,
        )
        self.assertIn("emhass_load_gap_contract.py", text)
        self.assertNotIn("config/site.env", text)
        self.assertNotIn("secrets_emhass", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the pin tests and verify they fail**

Run:

```bash
.venv/bin/python -m unittest \
  scripts.tests.test_emhass_image_pin -v
```

Expected: failure because Compose still uses `:latest` and the wrapper does not
exist.

- [ ] **Step 3: Create the installed-EMHASS contract probe**

Create `scripts/emhass_load_gap_contract.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import importlib.metadata
import logging

import numpy as np
import pandas as pd
from emhass.forecast import Forecast
from emhass.retrieve_hass import RetrieveHass
from zoneinfo import ZoneInfo


ENTITY_ID = "sensor.emhass_house_load_sample_w"
HORIZON = 32
GAP_START = 10
GAP_END_EXCLUSIVE = 16


def resample(missing_bins: set[int]) -> tuple[pd.DataFrame, np.ndarray]:
    bin_values = np.linspace(100.0, 720.0, HORIZON)
    index = pd.date_range(
        "2026-07-29T00:00:00Z",
        periods=HORIZON * 30,
        freq="1min",
    )
    raw = pd.DataFrame(
        {ENTITY_ID: np.repeat(bin_values, 30)},
        index=index,
    )
    missing_mask = np.zeros(len(raw), dtype=bool)
    for bin_index in missing_bins:
        missing_mask[bin_index * 30 : (bin_index + 1) * 30] = True
    raw = raw.loc[~missing_mask]
    resampled = Forecast.resample_data(
        raw,
        pd.Timedelta("30min"),
        pd.Timedelta("1min"),
    )
    return resampled, bin_values


def prepare(resampled: pd.DataFrame) -> pd.Series:
    retrieve = object.__new__(RetrieveHass)
    retrieve.df_final = resampled
    retrieve.var_list = [ENTITY_ID]
    retrieve.logger = logging.getLogger("emhass-load-gap-contract")
    retrieve.time_zone = ZoneInfo("UTC")
    prepared = retrieve.prepare_data(
        ENTITY_ID,
        load_negative=False,
        set_zero_min=False,
        var_replace_zero=[],
        var_interp=[ENTITY_ID],
    )
    if prepared is not True:
        raise AssertionError("RetrieveHass.prepare_data returned non-success")
    return retrieve.df_final[f"{ENTITY_ID}_positive"]


def native_naive(prepared: pd.Series) -> pd.Series:
    forecast = object.__new__(Forecast)
    forecast.forecast_dates = pd.date_range(
        "2026-07-30T00:00:00Z",
        periods=HORIZON,
        freq="30min",
    )
    result = forecast._get_load_forecast_naive(prepared.to_frame())
    if list(result.columns) != ["yhat"]:
        raise AssertionError(f"unexpected forecast columns: {list(result.columns)}")
    return result["yhat"]


def assert_output(output: pd.Series, expected: np.ndarray) -> None:
    if len(output) != HORIZON:
        raise AssertionError(f"forecast length {len(output)} != {HORIZON}")
    if not np.isfinite(output.to_numpy()).all():
        raise AssertionError("forecast contains a non-finite value")
    np.testing.assert_allclose(output.to_numpy(), expected)


def assert_case(missing_bins: set[int]) -> tuple[np.ndarray, np.ndarray]:
    resampled, bin_values = resample(missing_bins)
    expected = (
        pd.Series(resampled[ENTITY_ID].to_numpy())
        .interpolate(method="linear", limit=None)
        .to_numpy()
    )
    prepared = prepare(resampled)
    np.testing.assert_allclose(prepared.to_numpy(), expected)
    output = native_naive(prepared)
    assert_output(output, expected)
    return bin_values, expected


def main() -> int:
    complete_values, complete_expected = assert_case(set())
    np.testing.assert_allclose(complete_expected, complete_values)

    missing_bins = set(range(GAP_START, GAP_END_EXCLUSIVE))
    bin_values, expected = assert_case(missing_bins)
    interpolated = expected[GAP_START:GAP_END_EXCLUSIVE]
    lower = bin_values[GAP_START - 1]
    upper = bin_values[GAP_END_EXCLUSIVE]
    if not ((interpolated > lower) & (interpolated < upper)).all():
        raise AssertionError("interpolated values escaped their nonzero brackets")
    if (interpolated == 0).any():
        raise AssertionError("generic NaN-to-zero fill reached the load gap")

    incompatible = expected.copy()
    incompatible[GAP_START:GAP_END_EXCLUSIVE] = 0.0
    try:
        assert_output(pd.Series(incompatible), expected)
    except AssertionError:
        negative_control = "pass"
    else:
        raise AssertionError(
            "negative control did not reject zero-filled interpolation"
        )

    version = importlib.metadata.version("emhass")
    print(
        "EMHASS_LOAD_GAP_CONTRACT_OK "
        f"version={version} horizon={HORIZON} gap_bins="
        f"{GAP_END_EXCLUSIVE - GAP_START} "
        f"negative_control={negative_control}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Create the secret-free container wrapper**

Create `scripts/verify-emhass-load-gap-contract.sh`:

```bash
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
```

Run:

```bash
chmod +x scripts/verify-emhass-load-gap-contract.sh
python3 -m py_compile scripts/emhass_load_gap_contract.py
bash -n scripts/verify-emhass-load-gap-contract.sh
```

Expected: both syntax checks exit `0`.

- [ ] **Step 5: Pin the exact deployed release**

Change the `emhass` service in `docker-compose.yml` to:

```yaml
  emhass:
    image: ghcr.io/davidusb-geek/emhass:v0.17.5@sha256:0aa7c9062ed269c138b6099bb2b6d7428f640d5c00cdbf31b7e0067345779b8e
```

Do not change any volumes, environment, ports, health checks, or dependencies.

- [ ] **Step 6: Run the tests and compatibility probe**

Run:

```bash
.venv/bin/python -m unittest \
  scripts.tests.test_emhass_image_pin -v
./scripts/verify-emhass-load-gap-contract.sh
```

Expected:

```text
EMHASS_LOAD_GAP_CONTRACT_OK version=0.17.5 horizon=32 gap_bins=6 negative_control=pass
```

Both commands must exit `0`. A Docker pull, import error, assertion error,
non-finite value, zero fill, ordering change, or length change is a failed
contract.

- [ ] **Step 7: Document and enforce the upgrade procedure**

Create `docs/EMHASS_UPGRADES.md` with:

```markdown
# EMHASS upgrades

EMHASS is pinned by release tag and immutable digest in `docker-compose.yml`.
Never replace it with `latest`.

For every candidate:

1. Resolve and record the candidate's release tag, immutable manifest digest,
   package version, and source revision.
2. Run:
   `./scripts/verify-emhass-load-gap-contract.sh '<tag@sha256:digest>'`.
3. Run:
   `.venv/bin/python -m unittest scripts.tests.test_emhass_image_pin -v`.
4. Run the complete cron test suite.
5. Commit a note under `docs/verification/emhass/` containing the exact image,
   command, and passing output.
6. Only then change the Compose pin and deploy.

Release notes are supporting evidence, not a substitute for the compatibility
probe. A changed private EMHASS API fails the probe until the behavior and
probe are reviewed together.
```

Create `docs/verification/emhass/v0.17.5-load-gap-contract.md` with the pinned
image, source revision from Global Constraints, the exact commands from Step 6,
and the actual passing output produced in Step 6.

Add this subsection to `AGENTS.md` after `EMHASS invariants`:

```markdown
### EMHASS upgrades

- `docker-compose.yml` must use a release tag plus immutable digest, never
  `latest`.
- Before changing the EMHASS image, run
  `./scripts/verify-emhass-load-gap-contract.sh '<tag@sha256:digest>'`, the
  Compose pin test, and the full cron tests.
- Record passing output in `docs/verification/emhass/`. Do not infer native
  interpolation compatibility from release notes alone.
```

- [ ] **Step 8: Re-run Task 1 verification**

Run:

```bash
.venv/bin/python -m unittest \
  scripts.tests.test_emhass_image_pin -v
./scripts/verify-emhass-load-gap-contract.sh
python3 -m py_compile scripts/emhass_load_gap_contract.py
bash -n scripts/verify-emhass-load-gap-contract.sh
git diff --check -- \
  docker-compose.yml \
  scripts/emhass_load_gap_contract.py \
  scripts/verify-emhass-load-gap-contract.sh \
  scripts/tests/test_emhass_image_pin.py \
  docs/EMHASS_UPGRADES.md \
  docs/verification/emhass/v0.17.5-load-gap-contract.md \
  AGENTS.md
```

Expected: tests and probe pass, syntax checks exit `0`, and
`git diff --check` is silent.

- [ ] **Step 9: Commit Task 1 if authorized**

If implementation commits were explicitly authorized:

```bash
git add \
  docker-compose.yml \
  scripts/emhass_load_gap_contract.py \
  scripts/verify-emhass-load-gap-contract.sh \
  scripts/tests/test_emhass_image_pin.py \
  docs/EMHASS_UPGRADES.md \
  docs/verification/emhass/v0.17.5-load-gap-contract.md \
  AGENTS.md
git commit -m "build: pin and verify EMHASS load interpolation"
```

Otherwise leave these files unstaged and report the suggested commit message.

---

### Task 2: Classify complete, bounded, and unsafe load history

**Files:**

- Modify: `emhass-cron/fetch_prices_emhass.py`
- Modify: `emhass-cron/tests/test_fetch_prices_emhass.py`
- Modify: `scripts/tests/test_ha_emhass_sensors.py`

**Interfaces:**

- Consumes:
  `validate_load_history_records(records, prediction_horizon, step_min, now)`.
- Produces: a summary dictionary with exact keys:
  `mode`, `required_bins`, `covered_bins`, `missing_bin_count`,
  `missing_bins`, `interpolation_method`, `newest_sample_at`, and
  `newest_sample_age_minutes`; degraded summaries additionally contain
  `gap_start`, `gap_end_exclusive`, and `gap_duration_minutes`.
- Produces:
  `LoadHistoryError.summary: dict[str, Any] | None` for unsafe geometry.

- [ ] **Step 1: Add failing classifier tests**

In `NativeLoadHistoryValidationTests`, add a helper:

```python
def records_with_missing_bins(
    self,
    *,
    horizon: int,
    missing_indices: set[int],
) -> list[dict]:
    newest = datetime.fromisoformat("2026-07-26T15:32:00+02:00")
    first = newest - timedelta(minutes=30 * (horizon - 1))
    return [
        self.record(
            str(100 + index * 10),
            (first + timedelta(minutes=30 * index)).isoformat(),
        )
        for index in range(horizon)
        if index not in missing_indices
    ]
```

Add tests asserting:

```python
def test_complete_history_is_healthy(self):
    summary = fetch_prices_emhass.validate_load_history_records(
        self.records_with_missing_bins(horizon=32, missing_indices=set()),
        prediction_horizon=32,
        step_min=30,
        now=self.now,
    )
    self.assertEqual(summary["mode"], "healthy")
    self.assertEqual(summary["covered_bins"], 32)
    self.assertEqual(summary["missing_bin_count"], 0)
    self.assertEqual(summary["missing_bins"], [])
    self.assertIsNone(summary["interpolation_method"])

def test_six_bin_bracketed_interior_gap_is_degraded(self):
    summary = fetch_prices_emhass.validate_load_history_records(
        self.records_with_missing_bins(
            horizon=32,
            missing_indices=set(range(10, 16)),
        ),
        prediction_horizon=32,
        step_min=30,
        now=self.now,
    )
    self.assertEqual(summary["mode"], "degraded")
    self.assertEqual(summary["covered_bins"], 26)
    self.assertEqual(summary["missing_bin_count"], 6)
    self.assertEqual(summary["gap_duration_minutes"], 180)
    self.assertEqual(
        summary["interpolation_method"],
        "emhass_native_linear",
    )
    self.assertEqual(
        datetime.fromisoformat(summary["gap_end_exclusive"])
        - datetime.fromisoformat(summary["gap_start"]),
        timedelta(minutes=180),
    )

def test_seven_bin_gap_is_unsafe(self):
    with self.assertRaises(fetch_prices_emhass.LoadHistoryError) as raised:
        fetch_prices_emhass.validate_load_history_records(
            self.records_with_missing_bins(
                horizon=32,
                missing_indices=set(range(10, 17)),
            ),
            prediction_horizon=32,
            step_min=30,
            now=self.now,
        )
    self.assertEqual(raised.exception.summary["mode"], "unsafe")
    self.assertEqual(raised.exception.summary["missing_bin_count"], 7)

def test_stale_newest_sample_is_unsafe_with_diagnostics(self):
    with self.assertRaises(fetch_prices_emhass.LoadHistoryError) as raised:
        fetch_prices_emhass.validate_load_history_records(
            self.records_with_missing_bins(
                horizon=32,
                missing_indices=set(),
            ),
            prediction_horizon=32,
            step_min=30,
            now=datetime.fromisoformat(
                "2026-07-26T15:40:00+02:00"
            ),
        )
    self.assertEqual(raised.exception.summary["mode"], "unsafe")
    self.assertEqual(
        raised.exception.summary["newest_sample_age_minutes"],
        8.0,
    )
    self.assertEqual(raised.exception.summary["missing_bins"], [])

def test_multiple_gaps_are_unsafe(self):
    with self.assertRaises(fetch_prices_emhass.LoadHistoryError) as raised:
        fetch_prices_emhass.validate_load_history_records(
            self.records_with_missing_bins(
                horizon=32,
                missing_indices={5, 6, 20},
            ),
            prediction_horizon=32,
            step_min=30,
            now=self.now,
        )
    self.assertEqual(raised.exception.summary["mode"], "unsafe")

def test_oldest_and_newest_edge_gaps_are_unsafe(self):
    for missing_indices in ({0}, {31}):
        with self.subTest(missing_indices=missing_indices):
            with self.assertRaises(fetch_prices_emhass.LoadHistoryError):
                fetch_prices_emhass.validate_load_history_records(
                    self.records_with_missing_bins(
                        horizon=32,
                        missing_indices=missing_indices,
                    ),
                    prediction_horizon=32,
                    step_min=30,
                    now=self.now,
                )

def test_six_bins_remain_absolute_for_twelve_step_horizon(self):
    summary = fetch_prices_emhass.validate_load_history_records(
        self.records_with_missing_bins(
            horizon=12,
            missing_indices=set(range(3, 9)),
        ),
        prediction_horizon=12,
        step_min=30,
        now=self.now,
    )
    self.assertEqual(summary["mode"], "degraded")
    self.assertEqual(summary["missing_bin_count"], 6)
```

Add:

```python
def test_each_allowed_gap_length_is_degraded(self):
    for gap_length in range(1, 7):
        with self.subTest(gap_length=gap_length):
            start = 10
            summary = fetch_prices_emhass.validate_load_history_records(
                self.records_with_missing_bins(
                    horizon=32,
                    missing_indices=set(
                        range(start, start + gap_length)
                    ),
                ),
                prediction_horizon=32,
                step_min=30,
                now=self.now,
            )
            self.assertEqual(summary["mode"], "degraded")
            self.assertEqual(
                summary["missing_bin_count"],
                gap_length,
            )
```

Retain the existing freshness, valid-zero, invalid-value, boundary,
spring-gap, and autumn-fold tests. Replace their old whole-dictionary expected
values with the new exact keys from the Task 2 interface and assert
`mode=healthy` for accepted histories.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
.venv/bin/python -m unittest \
  emhass-cron.tests.test_fetch_prices_emhass.NativeLoadHistoryValidationTests \
  -v
```

Expected: failures because missing bins still raise unconditionally and healthy
summaries have no `mode`.

- [ ] **Step 3: Add the bounded classifier**

In `emhass-cron/fetch_prices_emhass.py`, replace the empty exception class and
add constants:

```python
MAX_DEGRADED_LOAD_GAP_BINS = 6
LOAD_HISTORY_MODE_HEALTHY = "healthy"
LOAD_HISTORY_MODE_DEGRADED = "degraded"
LOAD_HISTORY_MODE_UNSAFE = "unsafe"


class LoadHistoryError(RuntimeError):
    """Expected load-history config, retrieval, or validation failure."""

    def __init__(
        self,
        message: str,
        *,
        summary: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.summary = summary
```

In `validate_load_history_records`, keep timestamp parsing unchanged. Remove
the current early `newest_age_min > 5` raise so the rejected run can carry a
diagnostic summary. Replace the required/missing-bin branch with:

```python
required_bins_utc = [
    first_required_bin_utc + timedelta(minutes=step_min * index)
    for index in range(prediction_horizon)
]
required_set_utc = set(required_bins_utc)
missing_bins_utc = sorted(required_set_utc - covered_bins_utc)
summary: dict[str, Any] = {
    "mode": LOAD_HISTORY_MODE_HEALTHY,
    "required_bins": prediction_horizon,
    "covered_bins": len(covered_bins_utc & required_set_utc),
    "missing_bin_count": len(missing_bins_utc),
    "missing_bins": [value.isoformat() for value in missing_bins_utc],
    "interpolation_method": None,
    "newest_sample_at": newest_utc.isoformat(),
    "newest_sample_age_minutes": round(newest_age_min, 1),
}
if newest_age_min > 5:
    summary["mode"] = LOAD_HISTORY_MODE_UNSAFE
    raise LoadHistoryError(
        f"Load history newest finite sample is stale "
        f"({newest_age_min:.1f} minutes old; maximum 5)",
        summary=summary,
    )
if not missing_bins_utc:
    return summary

index_by_bin = {
    value: index for index, value in enumerate(required_bins_utc)
}
missing_indices = [index_by_bin[value] for value in missing_bins_utc]
is_consecutive = all(
    right == left + 1
    for left, right in zip(missing_indices, missing_indices[1:])
)
has_brackets = (
    missing_indices[0] > 0
    and missing_indices[-1] < prediction_horizon - 1
)
is_bounded = (
    1 <= len(missing_indices) <= MAX_DEGRADED_LOAD_GAP_BINS
)

if is_consecutive and has_brackets and is_bounded:
    gap_start = missing_bins_utc[0]
    gap_end_exclusive = missing_bins_utc[-1] + timedelta(minutes=step_min)
    summary.update(
        {
            "mode": LOAD_HISTORY_MODE_DEGRADED,
            "gap_start": gap_start.isoformat(),
            "gap_end_exclusive": gap_end_exclusive.isoformat(),
            "gap_duration_minutes": int(
                (gap_end_exclusive - gap_start).total_seconds() / 60
            ),
            "interpolation_method": "emhass_native_linear",
        }
    )
    return summary

summary["mode"] = LOAD_HISTORY_MODE_UNSAFE
rendered = ", ".join(
    value.astimezone(local_tz).isoformat()
    for value in missing_bins_utc[:4]
)
suffix = "..." if len(missing_bins_utc) > 4 else ""
reason = (
    "gap is not one consecutive sequence"
    if not is_consecutive
    else "gap is not bracketed inside the required window"
    if not has_brackets
    else (
        f"gap exceeds {MAX_DEGRADED_LOAD_GAP_BINS} "
        "30-minute bins"
    )
)
raise LoadHistoryError(
    f"Load history unsafe: {reason}; missing "
    f"{len(missing_bins_utc)} required 30-minute bin(s): "
    f"{rendered}{suffix}",
    summary=summary,
)
```

Update the `validate_native_load_history` log to include
`summary["mode"]`, `summary["covered_bins"]`, and
`summary["newest_sample_at"]`.

- [ ] **Step 4: Lock the existing HA live-source contract**

Extend `scripts/tests/test_ha_emhass_sensors.py`:

```python
def test_raw_house_load_requires_live_p1_battery_and_daylight_pv(self) -> None:
    sensor = self.raw_sensor()
    state = " ".join(str(sensor.get("state", "")).split())
    availability = " ".join(
        str(sensor.get("availability", "")).split()
    )
    self.assertIn("__P1_POWER_NET_ENTITY__", state)
    self.assertIn(
        "sensor.__SIGEN_PLANT_SLUG___plant_battery_power",
        state,
    )
    self.assertIn("__ENVOY_PV_ENTITY__", state)
    self.assertIn("__P1_POWER_NET_ENTITY__", availability)
    self.assertIn(
        "sensor.__SIGEN_PLANT_SLUG___plant_battery_power",
        availability,
    )
    self.assertIn("__ENVOY_PV_ENTITY__", availability)
    self.assertIn("20 * 60", availability)
    self.assertIn("sun_up or raw_pv_w > 50", availability)
```

This is a characterization test: it should pass without changing the HA YAML.
If it fails, stop and reconcile the live-input design rather than weakening
the assertion.

- [ ] **Step 5: Run classifier and HA contract tests**

Run:

```bash
.venv/bin/python -m unittest \
  emhass-cron.tests.test_fetch_prices_emhass.NativeLoadHistoryValidationTests \
  -v
.venv/bin/python -m unittest \
  scripts.tests.test_ha_emhass_sensors -v
```

Expected: all tests pass.

- [ ] **Step 6: Run the complete cron suite**

Run:

```bash
.venv/bin/python -m unittest discover \
  -s emhass-cron/tests -p 'test_*.py' -v
python3 -m py_compile emhass-cron/fetch_prices_emhass.py
git diff --check -- \
  emhass-cron/fetch_prices_emhass.py \
  emhass-cron/tests/test_fetch_prices_emhass.py \
  scripts/tests/test_ha_emhass_sensors.py
```

Expected: every test passes, compilation succeeds, and diff check is silent.

- [ ] **Step 7: Commit Task 2 if authorized**

If implementation commits were explicitly authorized:

```bash
git add \
  emhass-cron/fetch_prices_emhass.py \
  emhass-cron/tests/test_fetch_prices_emhass.py \
  scripts/tests/test_ha_emhass_sensors.py
git commit -m "feat: classify bounded MPC load-history gaps"
```

Otherwise leave these files unstaged and report the suggested commit message.

---

### Task 3: Carry load-history mode through dry-run, failures, and success

**Files:**

- Modify: `emhass-cron/fetch_prices_emhass.py`
- Modify: `emhass-cron/tests/test_fetch_prices_emhass.py`

**Interfaces:**

- Consumes: the Task 2 load-history summary.
- Produces:
  `finalize_successful_mpc_run(..., load_history: dict[str, Any])`.
- Produces:
  `_abort_mpc_load_history(..., summary: dict[str, Any] | None)`.
- Produces:
  `write_mpc_chain_failure(..., load_history: dict[str, Any])`.
- Persists the summary as `last_enever_run.json.mpc.load_history`.

- [ ] **Step 1: Write failing status-propagation tests**

Update `FinalizeSuccessfulMpcRunTests` to define:

```python
self.load_history = {
    "mode": "degraded",
    "required_bins": 32,
    "covered_bins": 26,
    "missing_bin_count": 6,
    "missing_bins": [
        "2026-07-29T02:30:00+00:00",
        "2026-07-29T03:00:00+00:00",
        "2026-07-29T03:30:00+00:00",
        "2026-07-29T04:00:00+00:00",
        "2026-07-29T04:30:00+00:00",
        "2026-07-29T05:00:00+00:00",
    ],
    "gap_start": "2026-07-29T02:30:00+00:00",
    "gap_end_exclusive": "2026-07-29T05:30:00+00:00",
    "gap_duration_minutes": 180,
    "interpolation_method": "emhass_native_linear",
    "newest_sample_at": "2026-07-29T05:49:00+00:00",
    "newest_sample_age_minutes": 1.0,
}
```

Pass `load_history=self.load_history` to
`finalize_successful_mpc_run` and assert the successful metadata is:

```python
{
    "prediction_horizon": 26,
    "plan_generated_at": self.completed_at.isoformat(),
    "load_history": self.load_history,
}
```

Add `import io` and `redirect_stdout` to the test imports, then add these
methods to `MpcLoadHistoryFailureTests`:

```python
@staticmethod
def degraded_summary() -> dict:
    return {
        "mode": "degraded",
        "required_bins": 3,
        "covered_bins": 2,
        "missing_bin_count": 1,
        "missing_bins": ["2026-07-26T13:00:00+00:00"],
        "gap_start": "2026-07-26T13:00:00+00:00",
        "gap_end_exclusive": "2026-07-26T13:30:00+00:00",
        "gap_duration_minutes": 30,
        "interpolation_method": "emhass_native_linear",
        "newest_sample_at": "2026-07-26T13:32:00+00:00",
        "newest_sample_age_minutes": 2.0,
    }

def test_degraded_history_runs_identical_native_payload(self):
    summary = self.degraded_summary()
    with tempfile.TemporaryDirectory() as tmp:
        share = Path(tmp)
        secrets = self.write_valid_inputs(share)
        with patch.object(
            fetch_prices_emhass,
            "validate_native_load_history",
            return_value=summary,
        ):
            exit_code = self.invoke_main(share, secrets)

    self.assertEqual(exit_code, 0)
    optim_calls = [
        item for item in self.emhass_action.call_args_list
        if item.args[1] == "naive-mpc-optim"
    ]
    self.assertEqual(len(optim_calls), 1)
    payload = optim_calls[0].args[2]
    self.assertEqual(payload["prediction_horizon"], 3)
    self.assertNotIn("load_power_forecast", payload)
    self.assertNotIn("alpha", payload)
    self.assertNotIn("beta", payload)
    self.assertEqual(
        self.finalizer.call_args.kwargs["load_history"],
        summary,
    )

def test_unsafe_history_persists_summary_and_skips_emhass(self):
    unsafe_summary = {
        "mode": "unsafe",
        "required_bins": 3,
        "covered_bins": 1,
        "missing_bin_count": 2,
        "missing_bins": [
            "2026-07-26T12:30:00+00:00",
            "2026-07-26T13:00:00+00:00",
        ],
        "interpolation_method": None,
        "newest_sample_at": "2026-07-26T13:32:00+00:00",
        "newest_sample_age_minutes": 2.0,
    }
    with tempfile.TemporaryDirectory() as tmp:
        share = Path(tmp)
        secrets = self.write_valid_inputs(share)
        with patch.object(
            fetch_prices_emhass,
            "validate_native_load_history",
            side_effect=fetch_prices_emhass.LoadHistoryError(
                "unsafe gap",
                summary=unsafe_summary,
            ),
        ):
            exit_code = self.invoke_main(share, secrets)
        status = self.assert_load_history_abort(share, exit_code)

    self.assertEqual(status["mpc"]["load_history"], unsafe_summary)

def test_dry_run_fetches_history_and_reports_mode_without_writes(self):
    summary = self.degraded_summary()
    with tempfile.TemporaryDirectory() as tmp:
        share = Path(tmp)
        secrets = self.write_valid_inputs(share)
        argv = [
            "fetch_prices_emhass.py",
            "--action",
            "mpc",
            "--dry-run",
            "--share-dir",
            str(share),
            "--secrets",
            str(secrets),
        ]
        stdout = io.StringIO()
        with (
            patch.object(
                fetch_prices_emhass,
                "fetch_soc_init",
                return_value=0.5,
            ),
            patch.object(
                fetch_prices_emhass,
                "fetch_soc_limit",
                return_value=0.05,
            ),
            patch.object(
                fetch_prices_emhass,
                "price_fetch_summary",
                return_value={"prediction_horizon_effective": 3},
            ),
            patch.object(
                fetch_prices_emhass,
                "validate_native_load_history",
                return_value=summary,
            ),
            patch.object(
                fetch_prices_emhass,
                "refresh_price_csvs_from_ha",
            ) as refresh_prices,
            patch.object(
                fetch_prices_emhass,
                "run_mpc_chain",
            ) as run_chain,
            patch.object(
                fetch_prices_emhass,
                "write_run_status",
            ) as write_status,
            patch.object(
                fetch_prices_emhass,
                "finalize_successful_mpc_run",
            ) as finalizer,
            patch.object(sys, "argv", argv),
            redirect_stdout(stdout),
        ):
            exit_code = fetch_prices_emhass.main()

        output = json.loads(stdout.getvalue())

    self.assertEqual(exit_code, 0)
    self.assertEqual(output["load_history"], summary)
    refresh_prices.assert_not_called()
    run_chain.assert_not_called()
    write_status.assert_not_called()
    finalizer.assert_not_called()
```

Change the existing contextlib import to:

```python
from contextlib import ExitStack, redirect_stdout
```

These tests use the existing fixture/patch helpers and introduce no real
network access.

- [ ] **Step 2: Run focused tests and verify they fail**

Run:

```bash
.venv/bin/python -m unittest \
  emhass-cron.tests.test_fetch_prices_emhass.FinalizeSuccessfulMpcRunTests \
  emhass-cron.tests.test_fetch_prices_emhass.MpcLoadHistoryFailureTests \
  -v
```

Expected: signature and metadata assertion failures because the summary is not
yet threaded through main/finalization.

- [ ] **Step 3: Thread summaries through success and abort paths**

Change the finalizer signature:

```python
def finalize_successful_mpc_run(
    secrets_path: Path,
    share_dir: Path,
    completed_at: datetime,
    *,
    prediction_horizon: int,
    load_history: dict[str, Any],
    credentials: tuple[str, str] | None = None,
) -> bool:
```

Add `"load_history": load_history` to both the successful `metadata` and the
`status_publish` failure metadata.

Change the load abort helper:

```python
def _abort_mpc_load_history(
    share_dir: Path,
    error: str,
    *,
    summary: dict[str, Any] | None = None,
) -> int:
    metadata = {"load_history": summary} if summary is not None else None
    write_run_status(
        share_dir,
        "mpc",
        ok=False,
        error=f"load history validation failed: {error}",
        failure_kind="load_history",
        metadata=metadata,
    )
    logging.error("MPC aborted: load history validation failed: %s", error)
    return 1
```

Every `except LoadHistoryError as exc` call must pass
`summary=exc.summary`.

Store the successful result:

```python
load_history_summary = validate_native_load_history(
    args.secrets,
    args.share_dir,
    prediction_horizon=prediction_horizon,
    tz_name=args.timezone,
    load_history_config=load_history_config,
    load_history_credentials=load_history_credentials,
)
```

Pass it to `finalize_successful_mpc_run`.

- [ ] **Step 4: Make dry-run classify history without mutating files**

In the current `if args.dry_run` branch, call
`validate_native_load_history` after resolving
`price_summary["prediction_horizon_effective"]`, using the preflighted config
and credential snapshot. Add:

```python
"load_history": load_history_summary,
```

to printed JSON. Keep the dry-run branch before CSV writes, weather refresh,
EMHASS calls, and status writes.

- [ ] **Step 5: Add causal degraded-load failure classification**

Add:

```python
LOAD_INTERPOLATION_FAILURE_KEYWORDS = (
    "get_load_forecast",
    "p_load_forecast",
    "get_mix_forecast",
    "load forecast",
)


def mpc_error_is_load_interpolation_related(error: str) -> bool:
    lowered = error.lower()
    return any(
        keyword in lowered
        for keyword in LOAD_INTERPOLATION_FAILURE_KEYWORDS
    )


def write_mpc_chain_failure(
    share_dir: Path,
    error: str,
    *,
    load_history: dict[str, Any],
) -> None:
    is_weather = mpc_error_is_weather_related(error)
    is_interpolation = (
        load_history.get("mode") == LOAD_HISTORY_MODE_DEGRADED
        and mpc_error_is_load_interpolation_related(error)
    )
    failure_kind = (
        "weather"
        if is_weather
        else "load_interpolation"
        if is_interpolation
        else None
    )
    write_run_status(
        share_dir,
        "mpc",
        ok=False,
        error=error,
        failure_kind=failure_kind,
        metadata={"load_history": load_history},
    )
    if is_weather:
        write_run_status(
            share_dir,
            "weather",
            ok=False,
            error=error,
            failure_kind="weather",
        )
```

Replace `write_mpc_weather_failures` calls in the real MPC exception branches
with `write_mpc_chain_failure`. Do not classify a generic optimizer error or a
bare `NaN` string as load interpolation; the error must contain a specific
load-forecast keyword. Preserve HTTP body truncation and current weather
classification.

Add this focused test class:

```python
class MpcChainFailureClassificationTests(unittest.TestCase):
    def setUp(self):
        self.share = Path("/tmp/mpc-chain-failure-share")
        self.degraded = {
            "mode": "degraded",
            "missing_bin_count": 1,
            "missing_bins": ["2026-07-29T03:00:00+00:00"],
        }
        self.healthy = {
            "mode": "healthy",
            "missing_bin_count": 0,
            "missing_bins": [],
        }

    @patch.object(fetch_prices_emhass, "write_run_status")
    def test_degraded_load_forecast_error_is_classified(
        self,
        mock_status,
    ):
        error = "naive-mpc-optim get_mix_forecast failed"
        fetch_prices_emhass.write_mpc_chain_failure(
            self.share,
            error,
            load_history=self.degraded,
        )
        mock_status.assert_called_once_with(
            self.share,
            "mpc",
            ok=False,
            error=error,
            failure_kind="load_interpolation",
            metadata={"load_history": self.degraded},
        )

    @patch.object(fetch_prices_emhass, "write_run_status")
    def test_weather_error_wins_over_degraded_load_classification(
        self,
        mock_status,
    ):
        error = "Open-Meteo get_mix_forecast request failed"
        fetch_prices_emhass.write_mpc_chain_failure(
            self.share,
            error,
            load_history=self.degraded,
        )
        self.assertEqual(
            mock_status.call_args_list,
            [
                call(
                    self.share,
                    "mpc",
                    ok=False,
                    error=error,
                    failure_kind="weather",
                    metadata={"load_history": self.degraded},
                ),
                call(
                    self.share,
                    "weather",
                    ok=False,
                    error=error,
                    failure_kind="weather",
                ),
            ],
        )

    @patch.object(fetch_prices_emhass, "write_run_status")
    def test_degraded_generic_solver_error_gets_no_invented_kind(
        self,
        mock_status,
    ):
        error = "optimizer returned infeasible"
        fetch_prices_emhass.write_mpc_chain_failure(
            self.share,
            error,
            load_history=self.degraded,
        )
        mock_status.assert_called_once_with(
            self.share,
            "mpc",
            ok=False,
            error=error,
            failure_kind=None,
            metadata={"load_history": self.degraded},
        )

    @patch.object(fetch_prices_emhass, "write_run_status")
    def test_healthy_load_error_is_not_degraded_interpolation(
        self,
        mock_status,
    ):
        error = "get_load_forecast failed"
        fetch_prices_emhass.write_mpc_chain_failure(
            self.share,
            error,
            load_history=self.healthy,
        )
        mock_status.assert_called_once_with(
            self.share,
            "mpc",
            ok=False,
            error=error,
            failure_kind=None,
            metadata={"load_history": self.healthy},
        )
```

Import `call` alongside the existing mock imports. In
`FinalizeSuccessfulMpcRunTests.test_marker_failure_records_pipeline_failure`,
pass `load_history=self.load_history` to the finalizer and change the expected
status call to:

```python
mock_status.assert_called_once_with(
    self.share,
    "mpc",
    ok=False,
    error="HA successful-MPC marker failed: HA unavailable",
    failure_kind="status_publish",
    metadata={"load_history": self.load_history},
)
```

- [ ] **Step 6: Run focused and complete tests**

Run:

```bash
.venv/bin/python -m unittest \
  emhass-cron.tests.test_fetch_prices_emhass.FinalizeSuccessfulMpcRunTests \
  emhass-cron.tests.test_fetch_prices_emhass.MpcLoadHistoryFailureTests \
  -v
.venv/bin/python -m unittest discover \
  -s emhass-cron/tests -p 'test_*.py' -v
python3 -m py_compile emhass-cron/fetch_prices_emhass.py
git diff --check -- \
  emhass-cron/fetch_prices_emhass.py \
  emhass-cron/tests/test_fetch_prices_emhass.py
```

Expected: all tests pass, compilation succeeds, and diff check is silent.

- [ ] **Step 7: Commit Task 3 if authorized**

If implementation commits were explicitly authorized:

```bash
git add \
  emhass-cron/fetch_prices_emhass.py \
  emhass-cron/tests/test_fetch_prices_emhass.py
git commit -m "feat: publish degraded MPC load-history status"
```

Otherwise leave these files unstaged and report the suggested commit message.

---

### Task 4: Make load-history notifications incident-aware

**Files:**

- Modify: `monitor/health_notify.py`
- Create: `monitor/tests/test_health_notify.py`

**Interfaces:**

- Consumes: `last_enever_run.json.mpc.load_history` from Task 3.
- Produces:
  `detect_load_history_transition(mpc, state) -> LoadHistoryTransition | None`.
- Persists the last delivered gap mode/signature under
  `_load_history_incident` in `monitor_alert_state.json`.
- Produces one entry notice and one recovery notice per gap signature.

- [ ] **Step 1: Create failing monitor unit tests**

Create `monitor/tests/test_health_notify.py` using `importlib.util` as the cron
tests do. Define:

```python
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "lib"))
MODULE_PATH = REPO_ROOT / "monitor" / "health_notify.py"
SPEC = importlib.util.spec_from_file_location("health_notify", MODULE_PATH)
health_notify = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = health_notify
SPEC.loader.exec_module(health_notify)


DEGRADED = {
    "ok": True,
    "failure_kind": None,
    "load_history": {
        "mode": "degraded",
        "missing_bins": [
            "2026-07-29T02:30:00+00:00",
            "2026-07-29T03:00:00+00:00",
        ],
        "missing_bin_count": 2,
        "gap_duration_minutes": 60,
    },
}
HEALTHY = {
    "ok": True,
    "load_history": {
        "mode": "healthy",
        "missing_bins": [],
        "missing_bin_count": 0,
    },
}
UNSAFE = {
    "ok": False,
    "failure_kind": "load_history",
    "error": "load history validation failed: unbracketed gap",
    "load_history": {
        "mode": "unsafe",
        "missing_bins": [
            "2026-07-29T02:30:00+00:00",
            "2026-07-29T03:00:00+00:00",
        ],
        "missing_bin_count": 2,
    },
}
```

Add tests:

```python
class LoadHistoryIncidentTests(unittest.TestCase):
    def test_first_degraded_mode_creates_one_transition(self):
        state = health_notify.AlertState()
        transition = health_notify.detect_load_history_transition(
            DEGRADED,
            state,
        )
        self.assertEqual(transition.mode, "degraded")
        health_notify.apply_load_history_transition(
            state,
            transition,
            notified_at=100.0,
        )
        self.assertIsNone(
            health_notify.detect_load_history_transition(DEGRADED, state)
        )

    def test_different_gap_is_a_new_incident(self):
        state = health_notify.AlertState(
            incident_mode="degraded",
            incident_signature=health_notify.load_history_signature(DEGRADED),
        )
        changed = json.loads(json.dumps(DEGRADED))
        changed["load_history"]["missing_bins"] = [
            "2026-07-30T02:30:00+00:00"
        ]
        self.assertIsNotNone(
            health_notify.detect_load_history_transition(changed, state)
        )

    def test_healthy_after_degraded_creates_one_recovery(self):
        state = health_notify.AlertState(
            incident_mode="degraded",
            incident_signature=health_notify.load_history_signature(DEGRADED),
        )
        transition = health_notify.detect_load_history_transition(
            HEALTHY,
            state,
        )
        self.assertEqual(transition.mode, "healthy")
        health_notify.apply_load_history_transition(
            state,
            transition,
            notified_at=200.0,
        )
        self.assertIsNone(
            health_notify.detect_load_history_transition(HEALTHY, state)
        )

    def test_unsafe_gap_is_primary_and_suppresses_derivatives(self):
        failures = [
            ("B2_mpc", "cron", "failed"),
            ("B3", "cron", "stale"),
            ("B5_cache", "weather", "cache stale"),
            ("B2_weather", "weather", "refresh failed"),
        ]
        filtered = health_notify.suppress_load_history_derivatives(
            failures,
            UNSAFE,
        )
        self.assertNotIn("B2_mpc", {item[0] for item in filtered})
        self.assertNotIn("B3", {item[0] for item in filtered})
        self.assertNotIn("B5_cache", {item[0] for item in filtered})
        self.assertIn("B2_weather", {item[0] for item in filtered})

    def test_old_numeric_state_file_still_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            (share / "monitor_alert_state.json").write_text(
                json.dumps({"B2_mpc": 123.0}),
                encoding="utf-8",
            )
            state = health_notify.load_alert_state(share)
        self.assertEqual(state["B2_mpc"].last_notify, 123.0)
```

- [ ] **Step 2: Run monitor tests and verify they fail**

Run:

```bash
.venv/bin/python -m unittest discover \
  -s monitor/tests -p 'test_*.py' -v
```

Expected: failures because transition/signature/suppression interfaces and
incident fields do not exist.

- [ ] **Step 3: Extend persisted alert state without breaking old files**

Add:

```python
import hashlib

LOAD_HISTORY_STATE_KEY = "_load_history_incident"


@dataclass
class AlertState:
    last_notify: float = 0.0
    consecutive_failures: int = 0
    incident_mode: str | None = None
    incident_signature: str | None = None


@dataclass(frozen=True)
class LoadHistoryTransition:
    mode: str
    signature: str | None
    alert_id: str
    title: str
    message: str
```

Teach `_parse_alert_entry` to read `incident_mode` and
`incident_signature`. Teach `persist_alert_state` to serialize
`LOAD_HISTORY_STATE_KEY` as:

```python
{
    "last_notify": entry.last_notify,
    "incident_mode": entry.incident_mode,
    "incident_signature": entry.incident_signature,
}
```

Keep numeric legacy entries and B5 consecutive-failure entries compatible.

- [ ] **Step 4: Implement pure incident transition helpers**

Add:

```python
def load_history_signature(mpc: dict) -> str:
    load_history = mpc.get("load_history")
    missing_bins = (
        load_history.get("missing_bins", [])
        if isinstance(load_history, dict)
        else []
    )
    payload = {
        "failure_kind": mpc.get("failure_kind"),
        "missing_bins": [str(value) for value in missing_bins],
    }
    if not payload["missing_bins"]:
        payload["error"] = str(mpc.get("error", ""))[:200]
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def detect_load_history_transition(
    mpc: dict,
    state: AlertState,
) -> LoadHistoryTransition | None:
    load_history = mpc.get("load_history")
    if not isinstance(load_history, dict):
        return None
    reported_mode = load_history.get("mode")
    mode: str | None = None
    if mpc.get("ok") is True and reported_mode == "degraded":
        mode = "degraded"
    elif (
        mpc.get("ok") is False
        and mpc.get("failure_kind") == "load_history"
    ):
        mode = "unsafe"
    elif mpc.get("ok") is True and reported_mode == "healthy":
        mode = "healthy"
    if mode is None:
        return None

    if mode == "healthy":
        if state.incident_mode not in ("degraded", "unsafe"):
            return None
        return LoadHistoryTransition(
            mode="healthy",
            signature=None,
            alert_id="B6_load_history_recovered",
            title="EMHASS load history recovered",
            message=(
                "Native MPC load history is healthy again; "
                "degraded/unsafe gap incident cleared."
            ),
        )

    signature = load_history_signature(mpc)
    if (
        state.incident_mode == mode
        and state.incident_signature == signature
    ):
        return None
    missing = int(load_history.get("missing_bin_count", 0) or 0)
    duration = int(load_history.get("gap_duration_minutes", 0) or 0)
    if mode == "degraded":
        return LoadHistoryTransition(
            mode=mode,
            signature=signature,
            alert_id="B6_load_history_degraded",
            title="EMHASS load history degraded",
            message=(
                f"MPC remains operational with native interpolation for "
                f"{missing} missing bin(s), {duration} minutes."
            ),
        )
    return LoadHistoryTransition(
        mode=mode,
        signature=signature,
        alert_id="B6_load_history_unsafe",
        title="EMHASS load history unsafe",
        message=(
            f"MPC is fail-closed for {missing} missing bin(s); "
            "Sigenergy remains in native self-consumption."
        ),
    )


def apply_load_history_transition(
    state: AlertState,
    transition: LoadHistoryTransition,
    *,
    notified_at: float,
) -> None:
    state.incident_mode = transition.mode
    state.incident_signature = transition.signature
    state.last_notify = notified_at


def suppress_load_history_derivatives(
    failures: list[tuple[str, str, str]],
    mpc: dict,
) -> list[tuple[str, str, str]]:
    if not (
        mpc.get("ok") is False
        and mpc.get("failure_kind") == "load_history"
    ):
        return failures
    suppressed = {"B2_mpc", "B3", "B5_cache"}
    return [
        failure for failure in failures
        if failure[0] not in suppressed
    ]
```

- [ ] **Step 5: Integrate transitions into monitor main**

Add a helper that reads only the MPC block:

```python
def read_mpc_block(share: Path) -> dict | None:
    path = share / "last_enever_run.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    mpc = data.get("mpc")
    return mpc if isinstance(mpc, dict) else None
```

Add a delivery helper so notification success is the only state mutation
point:

```python
def process_load_history_transition(
    alert_state: dict[str, AlertState],
    mpc: dict | None,
    *,
    base_url: str,
    token: str,
    service: str,
    now: float,
) -> bool:
    if mpc is None:
        return False
    incident = alert_state.setdefault(
        LOAD_HISTORY_STATE_KEY,
        AlertState(),
    )
    transition = detect_load_history_transition(mpc, incident)
    if transition is None:
        return False
    try:
        notify_ha(
            base_url,
            token,
            service,
            f"Energy: {transition.title}",
            transition.message,
        )
    except urllib.error.HTTPError as exc:
        logging.error(
            "HA notify failed %s: %s %s",
            transition.alert_id,
            exc.code,
            exc.read()[:200],
        )
        return False
    except Exception as exc:
        logging.error(
            "HA notify failed %s: %s",
            transition.alert_id,
            exc,
        )
        return False
    apply_load_history_transition(
        incident,
        transition,
        notified_at=now,
    )
    logging.info(
        "Notified HA: %s — %s",
        transition.alert_id,
        transition.message[:120],
    )
    return True
```

In `main`:

1. Load alert state before notification delivery.
2. Read `mpc = read_mpc_block(share)`.
3. Apply `suppress_load_history_derivatives` after existing B4 suppression.
4. Detect a load-history transition.
5. Call `process_load_history_transition` before generic cooldown alerts.
6. If delivery fails, leave incident state unchanged so the next monitor run
   retries.
7. Persist state once after transition and generic delivery attempts.

Do not route transition delivery through the two-hour cooldown:
state/signature transition detection is its deduplication mechanism.
Do not suppress `B2_weather`, B4, or B5-zero-fill.

- [ ] **Step 6: Add delivery retry and persistence tests**

Import `patch` from `unittest.mock`, then add:

```python
class LoadHistoryDeliveryTests(unittest.TestCase):
    def deliver(self, state, mpc, *, now):
        return health_notify.process_load_history_transition(
            state,
            mpc,
            base_url="http://ha/",
            token="token",
            service="notify.notify",
            now=now,
        )

    @patch.object(health_notify, "notify_ha")
    def test_success_persists_and_same_gap_deduplicates(
        self,
        mock_notify,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            state = {}
            self.assertTrue(self.deliver(state, DEGRADED, now=100.0))
            health_notify.persist_alert_state(share, state)
            reloaded = health_notify.load_alert_state(share)
            incident = reloaded[health_notify.LOAD_HISTORY_STATE_KEY]
            self.assertEqual(incident.incident_mode, "degraded")
            self.assertEqual(
                incident.incident_signature,
                health_notify.load_history_signature(DEGRADED),
            )
            self.assertFalse(
                self.deliver(reloaded, DEGRADED, now=101.0)
            )
        mock_notify.assert_called_once()

    @patch.object(
        health_notify,
        "notify_ha",
        side_effect=RuntimeError("HA unavailable"),
    )
    def test_failed_delivery_does_not_persist_and_retries(
        self,
        mock_notify,
    ):
        state = {}
        self.assertFalse(self.deliver(state, DEGRADED, now=100.0))
        incident = state[health_notify.LOAD_HISTORY_STATE_KEY]
        self.assertIsNone(incident.incident_mode)
        self.assertIsNone(incident.incident_signature)
        self.assertFalse(self.deliver(state, DEGRADED, now=101.0))
        self.assertEqual(mock_notify.call_count, 2)

    @patch.object(health_notify, "notify_ha")
    def test_healthy_after_degraded_delivers_one_recovery(
        self,
        mock_notify,
    ):
        incident = health_notify.AlertState(
            incident_mode="degraded",
            incident_signature=health_notify.load_history_signature(
                DEGRADED
            ),
        )
        state = {health_notify.LOAD_HISTORY_STATE_KEY: incident}
        self.assertTrue(self.deliver(state, HEALTHY, now=200.0))
        self.assertEqual(incident.incident_mode, "healthy")
        self.assertIsNone(incident.incident_signature)
        self.assertFalse(self.deliver(state, HEALTHY, now=201.0))
        mock_notify.assert_called_once()

    def test_unsafe_suppression_keeps_independent_weather_failure(self):
        failures = [
            ("B2_mpc", "cron", "load history failed"),
            ("B3", "cron", "stale MPC"),
            ("B5_cache", "weather", "derivative cache alert"),
            ("B2_weather", "weather", "independent refresh failed"),
        ]
        filtered = health_notify.suppress_load_history_derivatives(
            failures,
            UNSAFE,
        )
        self.assertEqual(
            filtered,
            [
                (
                    "B2_weather",
                    "weather",
                    "independent refresh failed",
                )
            ],
        )
```

These tests use temporary state files and patched HA delivery; they do not
call live HA or Docker. The first test also locks the
`_load_history_incident` JSON round trip.

- [ ] **Step 7: Run monitor and regression tests**

Run:

```bash
.venv/bin/python -m unittest discover \
  -s monitor/tests -p 'test_*.py' -v
.venv/bin/python -m unittest discover \
  -s emhass-cron/tests -p 'test_*.py' -v
python3 -m py_compile monitor/health_notify.py
git diff --check -- \
  monitor/health_notify.py \
  monitor/tests/test_health_notify.py
```

Expected: all tests pass, compilation succeeds, and diff check is silent.

- [ ] **Step 8: Commit Task 4 if authorized**

If implementation commits were explicitly authorized:

```bash
git add monitor/health_notify.py monitor/tests/test_health_notify.py
git commit -m "feat: deduplicate MPC load-history incidents"
```

Otherwise leave these files unstaged and report the suggested commit message.

---

### Task 5: Document the runtime contract and run full local verification

**Files:**

- Modify: `emhass-cron/README.md`
- Modify: `emhass/README.md`
- Modify: `memory-bank/techContext.md`

**Interfaces:**

- Consumes: Tasks 1 through 4 behavior.
- Produces: operator and agent documentation matching the executable contract.

- [ ] **Step 1: Update cron runtime documentation**

In `emhass-cron/README.md`, replace the statement that every required bin is
always mandatory with:

```markdown
Load history is classified per effective horizon:

- `healthy`: all required half-hour bins are covered;
- `degraded`: exactly one fully bracketed interior gap contains at most six
  consecutive bins and the newest composite sample is no older than five
  minutes;
- `unsafe`: longer, multiple, edge, unbracketed, stale, or malformed history.

Healthy and degraded runs use the same native `naive-mpc-optim` payload. Cron
does not send `load_power_forecast`, `alpha`, or `beta`; EMHASS performs its
configured in-memory linear interpolation. Unsafe history aborts before
optimization or publication. `last_enever_run.json` records the mode, missing
UTC bins, brackets, duration, method, and newest sample.
```

Retain UTC/DST, credential snapshot, price, weather, and successful-marker
documentation.

- [ ] **Step 2: Update EMHASS and memory-bank invariants**

In `emhass/README.md`, document the six-bin bounded native-interpolation policy
and link to `docs/EMHASS_UPGRADES.md`.

In `memory-bank/techContext.md`, replace:

```markdown
- Load forecast failure is fail-closed.
```

with an invariant that explicitly states:

```markdown
- Native load history is healthy with complete coverage and degraded only for
  one fully bracketed interior gap of at most six consecutive 30-minute bins
  after the composite P1/PV/battery-derived sample is fresh (<=5 min). Longer,
  multiple, edge, unbracketed, or stale gaps fail closed.
- Degraded mode still uses native `naive`; cron never sends a load list,
  `alpha`, or `beta`, and never writes synthetic HA recorder states.
- EMHASS is pinned by tag+digest. Every upgrade must pass
  `scripts/verify-emhass-load-gap-contract.sh` and record evidence before the
  Compose image changes.
```

Keep all unrelated memory-bank content unchanged.

- [ ] **Step 3: Run the complete local verification matrix**

Run:

```bash
./scripts/verify-emhass-load-gap-contract.sh
.venv/bin/python -m unittest discover \
  -s emhass-cron/tests -p 'test_*.py' -v
.venv/bin/python -m unittest discover \
  -s monitor/tests -p 'test_*.py' -v
.venv/bin/python -m unittest discover \
  -s scripts/tests -p 'test_*.py' -v
./scripts/render-ha-packages.sh
python3 -m py_compile \
  emhass-cron/fetch_prices_emhass.py \
  monitor/health_notify.py \
  scripts/emhass_load_gap_contract.py
bash -n \
  scripts/verify-emhass-load-gap-contract.sh \
  scripts/deploy-energy-stack.sh
git diff --check
```

Expected:

- the compatibility probe prints the exact success marker for 0.17.5;
- all cron, monitor, and script tests pass;
- HA rendering succeeds;
- Python and shell syntax checks exit `0`;
- `git diff --check` is silent.

- [ ] **Step 4: Review the scoped diff**

Run:

```bash
git status --short
git diff -- \
  docker-compose.yml \
  scripts/emhass_load_gap_contract.py \
  scripts/verify-emhass-load-gap-contract.sh \
  scripts/tests/test_emhass_image_pin.py \
  docs/EMHASS_UPGRADES.md \
  docs/verification/emhass/v0.17.5-load-gap-contract.md \
  AGENTS.md \
  emhass-cron/fetch_prices_emhass.py \
  emhass-cron/tests/test_fetch_prices_emhass.py \
  scripts/tests/test_ha_emhass_sensors.py \
  monitor/health_notify.py \
  monitor/tests/test_health_notify.py \
  emhass-cron/README.md \
  emhass/README.md \
  memory-bank/techContext.md
```

Expected: only the approved pin, compatibility contract, bounded gap policy,
status, monitoring, tests, and documentation appear. The pre-existing
`memory-bank/activeContext.md` change remains separate.

- [ ] **Step 5: Commit Task 5 if authorized**

If implementation commits were explicitly authorized:

```bash
git add \
  emhass-cron/README.md \
  emhass/README.md \
  memory-bank/techContext.md
git commit -m "docs: document degraded MPC load-history policy"
```

Otherwise leave these files unstaged and report the suggested commit message.

---

### Task 6: Deploy and verify natural healthy operation

**Files:**

- Modify after evidence exists: `memory-bank/activeContext.md`
- No synthetic fixture or live recorder file is created.

**Interfaces:**

- Consumes: reviewed Tasks 1 through 5 and `config/site.env`.
- Produces: deployed pinned EMHASS, rebuilt cron/monitor containers, natural
  scheduled-run evidence, and a rollback reference.

- [ ] **Step 1: Obtain explicit live-deploy authorization**

Before changing the energy host, show the user:

- the exact pinned image;
- complete local verification output;
- current dirty-worktree status;
- the deploy command;
- the fact that the deploy force-recreates EMHASS and rebuilds cron/monitor.

Proceed only after explicit approval for live deployment.

- [ ] **Step 2: Read site config and capture pre-deploy state**

Run:

```bash
SOURCE_SITE_ENV_ROOT="$PWD" source scripts/lib/source-site-env.sh
ssh "${ENERGY_REMOTE_HOST}" \
  "cd '${ENERGY_REMOTE_DIR}' &&
   docker inspect --format '{{.Config.Image}} {{.Image}}' emhass &&
   docker ps --format '{{.Names}} {{.Status}}' &&
   python3 - <<'PY'
import json
from pathlib import Path

status = json.loads(Path('emhass/share/last_enever_run.json').read_text())
print(json.dumps({
    'mpc': status.get('mpc'),
    'prices': status.get('prices'),
    'weather': status.get('weather'),
}, sort_keys=True))
PY"
```

Capture the pre-deploy HA control state through the energy host's existing
credentials without printing the token:

```bash
ssh "${ENERGY_REMOTE_HOST}" \
  "cd '${ENERGY_REMOTE_DIR}' && python3 - <<'PY'
import json
import urllib.request
from pathlib import Path

text = Path('emhass/secrets_emhass.yaml').read_text(encoding='utf-8')

def scalar(key):
    prefix = key + ':'
    for line in text.splitlines():
        if line.startswith(prefix):
            value = line.split(':', 1)[1].strip()
            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in (chr(34), chr(39))
            ):
                value = value[1:-1]
            return value
    raise RuntimeError('missing ' + key)

base = scalar('hass_url').rstrip('/') + '/'
token = scalar('long_lived_token')
entities = (
    'input_boolean.emhass_auto_control_master',
    'input_datetime.emhass_last_successful_mpc',
    'binary_sensor.emhass_mpc_fresh',
    'binary_sensor.emhass_control_allowed',
)
for entity_id in entities:
    request = urllib.request.Request(
        base + 'api/states/' + entity_id,
        headers={'Authorization': 'Bearer ' + token},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        state = json.load(response)
    print(json.dumps({
        'entity_id': entity_id,
        'state': state.get('state'),
        'reason': state.get('attributes', {}).get('reason'),
        'block_reason': state.get('attributes', {}).get('block_reason'),
        'age_min': state.get('attributes', {}).get('age_min'),
    }, sort_keys=True))
PY"
```

Expected: current image/status/container evidence is captured without printing
tokens or secrets. Record the master state for the post-deploy comparison.

- [ ] **Step 3: Create a recoverable remote backup**

Run:

```bash
backup_stamp="$(date +%Y%m%d-%H%M%S)"
SOURCE_SITE_ENV_ROOT="$PWD" source scripts/lib/source-site-env.sh
ssh "${ENERGY_REMOTE_HOST}" \
  "backup_dir=\"\${HOME}/energy-stack-backups/load-gap-${backup_stamp}\" &&
   mkdir -p \"\${backup_dir}\" &&
   cp '${ENERGY_REMOTE_DIR}/docker-compose.yml' \"\${backup_dir}/\" &&
   cp '${ENERGY_REMOTE_DIR}/emhass-cron/fetch_prices_emhass.py' \"\${backup_dir}/\" &&
   cp '${ENERGY_REMOTE_DIR}/monitor/health_notify.py' \"\${backup_dir}/\" &&
   echo \"\${backup_dir}\""
```

Record the exact printed backup directory. Do not use an unresolved or broad
path for rollback.

- [ ] **Step 4: Deploy through the repository script**

Run:

```bash
./scripts/deploy-energy-stack.sh
```

Expected: validation passes, files sync, cron/monitor build, pinned EMHASS is
pulled/recreated, and all three containers are running.

- [ ] **Step 5: Verify the deployed pin and isolated contract**

Run:

```bash
./scripts/verify-emhass-load-gap-contract.sh
SOURCE_SITE_ENV_ROOT="$PWD" source scripts/lib/source-site-env.sh
ssh "${ENERGY_REMOTE_HOST}" \
  "docker inspect --format '{{.Config.Image}} {{.Image}}' emhass"
```

The local wrapper starts the exact pinned image in an isolated one-shot
container and runs the contract. Verify the first field of remote
`docker inspect` output (`Config.Image`) exactly equals the pinned reference;
do not copy a probe into or modify the live container.

Expected deployed image:

```text
ghcr.io/davidusb-geek/emhass:v0.17.5@sha256:0aa7c9062ed269c138b6099bb2b6d7428f640d5c00cdbf31b7e0067345779b8e
```

- [ ] **Step 6: Verify one untouched `:00` and one untouched `:30` run**

After each boundary has elapsed, inspect once rather than holding an
interactive wait:

```bash
SOURCE_SITE_ENV_ROOT="$PWD" source scripts/lib/source-site-env.sh
ssh "${ENERGY_REMOTE_HOST}" \
  "docker logs --since '45m' emhass-cron 2>&1 |
   grep -E 'HA load history|mode|naive-mpc-optim|publish-data|job succeeded|failed'"
ssh "${ENERGY_REMOTE_HOST}" \
  "docker logs --since '45m' emhass 2>&1 |
   grep -E 'Optimal|NaN|Traceback|get_mix_forecast|load forecast' || true"
ssh "${ENERGY_REMOTE_HOST}" \
  "cd '${ENERGY_REMOTE_DIR}' && python3 - <<'PY'
import json
from pathlib import Path

status = json.loads(
    Path('emhass/share/last_enever_run.json').read_text(encoding='utf-8')
)
print(json.dumps(status.get('mpc'), sort_keys=True))
PY"
```

Repeat the read-only HA-state query from Step 2 and compare its four JSON rows
with the pre-deploy capture. This provides direct evidence for the successful
marker, MPC freshness, control gate, and unchanged master state without
printing credentials.

Pass criteria:

- load history reports `mode=healthy`;
- native payload contains no cron-supplied load list, `alpha`, or `beta`;
- optimization reports `Optimal`;
- publish succeeds;
- scheduled job succeeds;
- `last_enever_run.json.mpc.ok=true`;
- `mpc.load_history.mode=healthy`;
- successful marker advances;
- master state is unchanged and control freshness is `on`;
- no NaN, traceback, or mix-forecast error occurs.

- [ ] **Step 7: Verify unsafe/degraded policy only through fixtures**

Re-run:

```bash
.venv/bin/python -m unittest \
  emhass-cron.tests.test_fetch_prices_emhass.NativeLoadHistoryValidationTests \
  -v
.venv/bin/python -m unittest discover \
  -s monitor/tests -p 'test_*.py' -v
./scripts/verify-emhass-load-gap-contract.sh
```

Expected: synthetic one-to-six-bin gaps pass as degraded, unsafe geometries
abort, incident tests pass, and the upstream contract passes. Do not create a
live outage.

- [ ] **Step 8: Update active context with actual evidence**

Surgically update `memory-bank/activeContext.md` without replacing its existing
2026-07-29 incident section. Record:

- pinned image tag, digest, package version, and source revision;
- local test/probe totals;
- deployment timestamp;
- remote backup directory;
- `:00` and `:30` healthy-run timestamps;
- plan freshness/control state;
- the fact that degraded/unsafe geometry was fixture-tested, not live-induced;
- any remaining observation window.

Do not claim a degraded live recovery occurred unless a natural future outage
actually exercises it.

- [ ] **Step 9: Commit the evidence update if authorized**

If implementation commits were explicitly authorized:

```bash
git add memory-bank/activeContext.md
git commit -m "docs: record bounded load-gap deployment verification"
```

Before staging, confirm the commit does not absorb unrelated pre-existing
memory-bank edits without the user's approval. Otherwise leave the evidence
update unstaged.

## Rollback

If the pinned image does not start, healthy history is misclassified, an
untouched boundary run fails, MPC produces NaN/traceback output, publication
fails, or control freshness does not recover:

1. Turn off the existing EMHASS master through normal Home Assistant operator
   control.
2. Verify Sigenergy is in native maximum self-consumption with confirmed
   charge/discharge limits.
3. Restore only the exact files from the backup directory printed in Task 6
   Step 3.
4. In `${ENERGY_REMOTE_DIR}`, rebuild `emhass-cron` and `energy-monitor`, then
   force-recreate `emhass`, `emhass-cron`, and `energy-monitor` through the
   configured Compose project.
5. Verify containers, native mode, successful-marker state, and current
   `last_enever_run.json`.
6. Record the failed criterion and rollback evidence in
   `memory-bank/activeContext.md`.

Do not restore a 300 W constant, runtime load list, cached load forecast,
Zonneplan load substitute, or synthetic HA history during rollback.
