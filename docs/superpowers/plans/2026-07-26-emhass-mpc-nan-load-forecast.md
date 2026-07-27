# EMHASS Native Load Forecast and Fail-Closed MPC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the temporary 300 W load list and NaN-prone tariff placeholder with a one-minute HA load-history sensor and EMHASS's native naive forecast, failing closed on missing data.

**Architecture:** Home Assistant samples the existing calculated house-load sensor at a regular one-minute cadence. EMHASS retrieves and interpolates that bounded history using its configured native `naive` method; cron supplies prices, weather-cache policy, horizon, and SOC only. No code fabricates load when HA or history is unavailable—the existing 40-minute successful-plan gate restores Sigenergy native control.

**Tech Stack:** Home Assistant trigger template YAML, EMHASS 0.17.5, Python 3 stdlib, `unittest`, JSON configuration, Docker Compose deployment scripts.

## Global Constraints

- Read `memory-bank/activeContext.md` and `memory-bank/techContext.md` before implementation.
- Read `.agents/skills/emhass-energy-stack/SKILL.md` before live work.
- Use `superpowers:using-git-worktrees` before implementation because the source worktree may contain unrelated user changes; deploy only from a reviewed isolated worktree.
- Source `config/site.env` before SSH, deployment, or live diagnostics; never hardcode hosts, usernames, tokens, or plant IDs.
- Preserve unrelated worktree changes. Do not create a git commit unless the user explicitly requests one.
- Keep `optimization_time_step` at 30 minutes and `prediction_horizon` at 32 maximum.
- Keep price forecast methods `csv`, weather `open-meteo`, and `open_meteo_cache_max_age` at 120 minutes.
- Price failure continues to abort MPC; weather cache retry continues to call `clear_weather_cache_files()`.
- Do not add any fixed, dynamic, or cached load fallback.
- Do not send `load_power_forecast`, `alpha`, or `beta` to `naive-mpc-optim`.
- Do not switch EMHASS until the sampled sensor has at least 16 hours of usable history.
- HA deployment must use `./scripts/deploy-home-assistant.sh`; energy-host deployment must use `./scripts/deploy-energy-stack.sh`.

## File Map

| File | Responsibility |
|---|---|
| `home-assistant/ha_emhass_sensors.yaml` | Define the one-minute sampled W sensor. |
| `scripts/tests/test_ha_emhass_sensors.py` | Assert cadence, units, source, availability, and forced recorder updates. |
| `docs/ENTITY_CONTRACT.md` | Declare the new stable entity and distinguish raw from sampled load. |
| `emhass-cron/fetch_prices_emhass.py` | Remove temporary load fetching/list construction and send the native payload. |
| `emhass-cron/tests/test_fetch_prices_emhass.py` | Replace 300 W/fallback tests with native payload and failure-status coverage. |
| `emhass-cron/README.md` | Document native load history and fail-closed behavior. |
| `emhass/share/config.json.example` | Wire native naive load history to the sampled sensor. |
| `emhass/README.md` | Remove the recorder-workaround/300 W instructions and document cutover prerequisites. |
| `memory-bank/activeContext.md` | Record implementation and deployment state after significant changes. |
| `memory-bank/techContext.md` | Record the no-fabricated-load invariant. |

---

### Task 1: Add the bounded Home Assistant load-history sensor

**Files:**

- Modify: `home-assistant/ha_emhass_sensors.yaml`
- Create: `scripts/tests/test_ha_emhass_sensors.py`
- Modify: `docs/ENTITY_CONTRACT.md`

**Interfaces:**

- Consumes: `sensor.emhass_house_load_power_w`, already rendered from P1, PV, and battery inputs.
- Produces: `sensor.emhass_house_load_sample_w`, numeric watts with one trigger per minute and a changing `sampled_at` attribute.

- [ ] **Step 1: Inspect the existing HA rendering and test conventions**

Run:

```bash
sed -n '1,130p' home-assistant/ha_emhass_sensors.yaml
sed -n '1,180p' scripts/tests/test_sigen_ems_package.py
sed -n '1,100p' scripts/render-ha-packages.sh
```

Expected: the source house-load sensor exists, package rendering replaces site
placeholders, and tests use Python `unittest`.

- [ ] **Step 2: Write the failing sensor-structure tests**

Create `scripts/tests/test_ha_emhass_sensors.py` with:

```python
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "home-assistant" / "ha_emhass_sensors.yaml"


class EmhassLoadSampleSensorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = PACKAGE.read_text(encoding="utf-8")
        marker = "default_entity_id: sensor.emhass_house_load_sample_w"
        marker_at = cls.text.find(marker)
        if marker_at < 0:
            cls.sample_block = ""
            return
        block_start = cls.text.rfind("  - trigger:", 0, marker_at)
        block_end = cls.text.find("\n  - ", marker_at)
        cls.sample_block = cls.text[
            block_start : len(cls.text) if block_end < 0 else block_end
        ]

    def test_sample_sensor_uses_minute_trigger(self):
        self.assertIn("  - trigger:", self.sample_block)
        self.assertIn("platform: time_pattern", self.sample_block)
        self.assertIn('seconds: "0"', self.sample_block)

    def test_sample_sensor_has_power_metadata(self):
        self.assertIn(
            "default_entity_id: sensor.emhass_house_load_sample_w",
            self.sample_block,
        )
        self.assertIn("unit_of_measurement: W", self.sample_block)
        self.assertIn("device_class: power", self.sample_block)
        self.assertIn("state_class: measurement", self.sample_block)

    def test_sample_sensor_uses_numeric_source_and_forces_update(self):
        self.assertIn("sensor.emhass_house_load_power_w", self.sample_block)
        self.assertIn("float(none)", self.sample_block)
        self.assertIn("sampled_at:", self.sample_block)
        self.assertIn("now().isoformat()", self.sample_block)


if __name__ == "__main__":
    unittest.main()
```

The test intentionally uses only the standard library. Package rendering in
Step 6 remains the YAML syntax/merge check.

- [ ] **Step 3: Run the new tests and confirm they fail**

Run:

```bash
python3 scripts/tests/test_ha_emhass_sensors.py -v
```

Expected: failure because `sensor.emhass_house_load_sample_w` does not exist.

- [ ] **Step 4: Add the trigger-based sampled sensor**

Append a second item under the existing top-level `template:` list in
`home-assistant/ha_emhass_sensors.yaml`:

```yaml
  - trigger:
      - platform: time_pattern
        seconds: "0"
    sensor:
      - name: EMHASS house load sample
        unique_id: emhass_house_load_sample_w
        default_entity_id: sensor.emhass_house_load_sample_w
        unit_of_measurement: W
        device_class: power
        state_class: measurement
        state: >
          {{ states('sensor.emhass_house_load_power_w') | float(0) | round(0) }}
        availability: >
          {% set value = states('sensor.emhass_house_load_power_w') %}
          {{ value not in ['unknown', 'unavailable', 'none', '']
             and (value | float(none)) is not none }}
        attributes:
          sampled_at: "{{ now().isoformat() }}"
```

Do not replace the existing raw calculated sensor; dashboards and monitoring
may continue using it.

- [ ] **Step 5: Document the entity contract**

In `docs/ENTITY_CONTRACT.md`:

- update the `Last updated` date to `2026-07-26`;
- describe `sensor.emhass_house_load_power_w` as instantaneous calculated load;
- add `sensor.emhass_house_load_sample_w` as the EMHASS history/naive-forecast
  source with one-minute cadence and W units.

The required-entities table must not claim that the raw high-churn sensor is
the MPC history source.

- [ ] **Step 6: Run HA sensor tests and render packages**

Run:

```bash
python3 scripts/tests/test_ha_emhass_sensors.py -v
./scripts/render-ha-packages.sh
```

Expected: tests pass and rendering exits `0` without unresolved placeholders.

---

### Task 2: Remove the 300 W runtime forecast and use native EMHASS input

**Files:**

- Modify: `emhass-cron/fetch_prices_emhass.py`
- Modify: `emhass-cron/tests/test_fetch_prices_emhass.py`

**Interfaces:**

- Consumes: effective prediction horizon and optional SOC values.
- Produces: `build_naive_mpc_payload(...) -> dict[str, Any]` containing no runtime load forecast or mixing weights.

- [ ] **Step 1: Replace the temporary-load tests with native-payload tests**

Delete `LoadForecastTests` and `FetchCurrentLoadTests` from
`emhass-cron/tests/test_fetch_prices_emhass.py`. Replace
`NaiveMpcPayloadTests` with:

```python
class NaiveMpcPayloadTests(unittest.TestCase):
    def test_payload_uses_configured_native_load_forecast(self):
        payload = fetch_prices_emhass.build_naive_mpc_payload(
            prediction_horizon=3,
            soc_init=0.42,
            soc_final=0.6,
        )

        self.assertEqual(
            payload,
            {
                "weather_forecast_cache_only": True,
                "prediction_horizon": 3,
                "soc_init": 0.42,
                "soc_final": 0.6,
            },
        )
        self.assertNotIn("load_power_forecast", payload)
        self.assertNotIn("alpha", payload)
        self.assertNotIn("beta", payload)

    def test_payload_keeps_soc_optional(self):
        payload = fetch_prices_emhass.build_naive_mpc_payload(
            prediction_horizon=2,
            soc_init=None,
            soc_final=None,
        )

        self.assertEqual(
            payload,
            {
                "weather_forecast_cache_only": True,
                "prediction_horizon": 2,
            },
        )
```

Remove the now-unused `math` import from the test file. Update any
`run_mpc_chain` tests/calls to remove `first_slot_load`.

- [ ] **Step 2: Add a source-level regression test for removed fallback code**

Add:

```python
class NativeLoadForecastSourceTests(unittest.TestCase):
    def test_temporary_load_fallback_is_absent(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        forbidden = [
            "LOAD_FORECAST_CONSTANT_W",
            "LOAD_FORECAST_MODE",
            "LOAD_FORECAST_MIX_ALPHA",
            "LOAD_FORECAST_MIX_BETA",
            "def fetch_current_load(",
            "def build_load_power_forecast(",
            "--load-sensor",
            "load_power_forecast",
        ]
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, source)
```

This test makes reintroducing a silent fabricated-load fallback explicit.

- [ ] **Step 3: Run the focused tests and confirm they fail**

Run:

```bash
python3 -m unittest emhass-cron.tests.test_fetch_prices_emhass.NativeLoadForecastSourceTests -v
python3 -m unittest emhass-cron.tests.test_fetch_prices_emhass.NaiveMpcPayloadTests -v
```

If the hyphenated directory prevents dotted imports, run the file directly:

```bash
python3 emhass-cron/tests/test_fetch_prices_emhass.py -v
```

Expected: failures while the constants, functions, CLI option, and list payload
remain.

- [ ] **Step 4: Delete the temporary load path**

In `emhass-cron/fetch_prices_emhass.py`:

- remove `import math` if no other use remains;
- remove all four `LOAD_FORECAST_*` constants;
- delete `fetch_current_load`;
- delete `build_load_power_forecast`;
- remove `first_slot_load` from `build_naive_mpc_payload`;
- remove `first_slot_load` from `run_mpc_chain`;
- delete the `--load-sensor` parser option;
- delete the `fetch_current_load(...)` call in the MPC action;
- remove `first_slot_load`, `load_forecast_mode`, and
  `load_forecast_constant_w` from dry-run JSON;
- remove load forecast metadata from `finalize_successful_mpc_run` and any
  failure/status path.

Keep the payload builder:

```python
def build_naive_mpc_payload(
    *,
    prediction_horizon: int,
    soc_init: float | None,
    soc_final: float | None,
    soc_min: float | None = None,
    soc_max: float | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "weather_forecast_cache_only": True,
        "prediction_horizon": prediction_horizon,
    }
    if soc_init is not None:
        payload["soc_init"] = soc_init
    if soc_final is not None:
        payload["soc_final"] = soc_final
    if soc_min is not None:
        payload["battery_minimum_state_of_charge"] = soc_min
    if soc_max is not None:
        payload["battery_maximum_state_of_charge"] = soc_max
    return payload
```

Change the MPC log message to:

```python
logging.info("MPC: naive-mpc-optim (CSV prices, cache-only weather, native naive load)")
```

- [ ] **Step 5: Verify failed optimization still cannot mark success**

Inspect and retain the existing control flow:

```bash
sed -n '740,825p' emhass-cron/fetch_prices_emhass.py
sed -n '1030,1140p' emhass-cron/fetch_prices_emhass.py
```

Expected: `run_mpc_chain` calls optimization before publish; an exception writes
failure and returns `1`; `finalize_successful_mpc_run` is called only after the
chain completes.

- [ ] **Step 6: Run the full cron test suite**

Run:

```bash
python3 -m unittest discover -s emhass-cron/tests -p 'test_*.py' -v
```

Expected: all tests pass and no test asserts a 300 W fallback.

---

### Task 3: Wire EMHASS history to the sampled sensor and update operator docs

**Files:**

- Modify: `emhass/share/config.json.example`
- Modify: `emhass/README.md`
- Modify: `emhass-cron/README.md`

**Interfaces:**

- Consumes: `sensor.emhass_house_load_sample_w` from Task 1.
- Produces: native EMHASS configuration with bounded load history and no runtime list override.

- [ ] **Step 1: Update the EMHASS example configuration**

In `emhass/share/config.json.example`:

- replace the obsolete workaround note with a note that native load history
  requires the one-minute sampled sensor;
- set `sensor_power_photovoltaics` and
  `sensor_power_photovoltaics_forecast` to
  `sensor.emhass_pv_power_w`;
- set `sensor_power_load_no_var_loads` and `var_model` to
  `sensor.emhass_house_load_sample_w`;
- set `sensor_linear_interp` to both sampled load and actual PV;
- keep `sensor_replace_zero` limited to actual PV;
- keep `load_forecast_method` as `naive`;
- keep `historic_days_to_retrieve: 1`, 30-minute steps, and horizon 32.

Use this exact sensor section:

```json
  "sensor_power_photovoltaics": "sensor.emhass_pv_power_w",
  "sensor_power_photovoltaics_forecast": "sensor.emhass_pv_power_w",
  "sensor_power_load_no_var_loads": "sensor.emhass_house_load_sample_w",
  "sensor_replace_zero": [
    "sensor.emhass_pv_power_w"
  ],
  "sensor_linear_interp": [
    "sensor.emhass_house_load_sample_w",
    "sensor.emhass_pv_power_w"
  ],
```

Do not add the load sensor to `sensor_replace_zero`.

- [ ] **Step 2: Replace obsolete load documentation**

In `emhass/README.md`, replace the 300 W/placeholder paragraph with:

```markdown
**Load forecast:** EMHASS uses its configured native `naive` method with
`sensor.emhass_house_load_sample_w` as load history. The HA trigger sensor
records the calculated house load once per minute in watts. Do not pass a
runtime `load_power_forecast` list and do not substitute a price or power-flow
entity. Allow at least 16 hours of sampled history before switching a 32-step,
30-minute horizon. After changing sensor keys, delete `/data/params.pkl` or
restart `emhass`.

Load/history failure is fail-closed: the MPC run fails and does not advance
`input_datetime.emhass_last_successful_mpc`; HA's 40-minute freshness gate
restores Sigenergy native control. There is no constant-load fallback.
```

In `emhass-cron/README.md`, describe the MPC chain as:

```text
HA prices -> weather validation -> native naive load history ->
naive-mpc-optim -> publish-data -> successful-MPC marker
```

State explicitly that the successful marker changes only after optimization
and publish both succeed.

- [ ] **Step 3: Add configuration regression assertions**

Extend the most appropriate existing configuration test under
`emhass-cron/tests` or `scripts/tests` with:

```python
config = json.loads(
    (REPO_ROOT / "emhass/share/config.json.example").read_text(encoding="utf-8")
)
self.assertEqual(config["load_forecast_method"], "naive")
self.assertEqual(
    config["sensor_power_load_no_var_loads"],
    "sensor.emhass_house_load_sample_w",
)
self.assertEqual(config["var_model"], "sensor.emhass_house_load_sample_w")
self.assertIn(
    "sensor.emhass_house_load_sample_w",
    config["sensor_linear_interp"],
)
self.assertNotIn(
    "sensor.emhass_house_load_sample_w",
    config["sensor_replace_zero"],
)
```

Do not create a test that reads gitignored live `config.json`.

- [ ] **Step 4: Validate JSON and scan for obsolete behavior**

Run:

```bash
python3 -m json.tool emhass/share/config.json.example >/dev/null
rg -n "constant_300|LOAD_FORECAST_|def fetch_current_load|def build_load_power_forecast|--load-sensor" \
  emhass-cron/fetch_prices_emhass.py emhass-cron/tests
rg -n '"load_power_forecast"|"alpha"|"beta"|sensor.zonneplan_current_electricity_tariff' \
  emhass/share/config.json.example
```

Expected: JSON is valid and both `rg` commands exit `1` with no matches. The
operator documentation may name removed fields while explaining that they are
forbidden; executable code and configuration may not contain them.

- [ ] **Step 5: Run all relevant local checks**

Run:

```bash
python3 -m unittest discover -s emhass-cron/tests -p 'test_*.py' -v
python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v
./scripts/render-ha-packages.sh
git diff --check
```

Expected: all tests pass, HA packages render, and the diff has no whitespace
errors.

---

### Task 4: Deploy in two stages and verify fail-closed operation

**Files:**

- Modify after verification: `memory-bank/activeContext.md`
- Modify if a new invariant is established: `memory-bank/techContext.md`

**Interfaces:**

- Consumes: Tasks 1–3 passing locally and `config/site.env`.
- Produces: a live sampled sensor, then a live native-naive MPC cutover with evidence from both `:00` and `:30` runs.

- [ ] **Step 1: Re-read live site configuration and capture pre-deploy state**

Run:

```bash
source scripts/lib/source-site-env.sh
git status --short
git diff -- home-assistant emhass-cron emhass monitor docker-compose.yml
ssh "${ENERGY_REMOTE_HOST}" \
  "docker ps --filter name=emhass --filter name=emhass-cron --filter name=energy-monitor"
```

Record without exposing secrets:

- current master/control state;
- last successful MPC timestamp;
- current EMHASS container health;
- the live `sensor_power_load_no_var_loads`, `var_model`,
  `sensor_linear_interp`, and `sensor_replace_zero` keys.

Stop if the isolated worktree contains unrelated changes in a deployment
scope. Do not alter `input_boolean.emhass_auto_control_master`.

- [ ] **Step 2: Deploy only the HA sampled sensor**

Temporarily ensure the energy-host changes are not deployed by using the HA
deployment script only:

```bash
./scripts/deploy-home-assistant.sh
```

Expected: render succeeds, the HA container configuration check succeeds, the
package deploy completes, and HA restarts successfully.

- [ ] **Step 3: Verify the new sensor is healthy**

Query HA using the configured host/token mechanism without printing the token.
Verify:

- entity exists as `sensor.emhass_house_load_sample_w`;
- unit is `W`;
- device class is `power`;
- state class is `measurement`;
- state is finite and non-negative;
- `sampled_at` advances on consecutive minutes;
- history begins accumulating no more than one record per minute, apart from
  restart/reload transitions.

If any check fails, stop. Do not cut over EMHASS.

- [ ] **Step 4: Wait for the history gate**

Do not use a blocking sleep. End the deployment session or monitor
periodically until the sensor has at least 16 hours of usable history.

Then query the HA history endpoint for the most recent 16 hours and verify:

- HTTP 200;
- enough data to populate all 32 half-hour bins;
- no `unknown`, `unavailable`, empty, or non-finite value remains after the
  expected interpolation;
- timestamps cover both `:00` and `:30` boundaries.

If the gate is not satisfied, leave the old energy-host configuration active.

- [ ] **Step 5: Back up the deployed energy code and configuration**

Create a targeted, timestamped backup before rsync changes the energy host:

```bash
DEPLOY_STAMP="$(date +%Y%m%d-%H%M%S)"
ENERGY_BACKUP_DIR="${ENERGY_REMOTE_DIR}/backups/native-load-${DEPLOY_STAMP}"
ssh "${ENERGY_REMOTE_HOST}" "
  mkdir -p '${ENERGY_BACKUP_DIR}/emhass/share' '${ENERGY_BACKUP_DIR}/emhass-cron' &&
  cp '${ENERGY_REMOTE_DIR}/emhass/share/config.json' \
     '${ENERGY_BACKUP_DIR}/emhass/share/config.json' &&
  cp '${ENERGY_REMOTE_DIR}/emhass-cron/fetch_prices_emhass.py' \
     '${ENERGY_BACKUP_DIR}/emhass-cron/fetch_prices_emhass.py' &&
  cp '${ENERGY_REMOTE_DIR}/emhass-cron/crontab' \
     '${ENERGY_BACKUP_DIR}/emhass-cron/crontab' &&
  cp '${ENERGY_REMOTE_DIR}/docker-compose.yml' \
     '${ENERGY_BACKUP_DIR}/docker-compose.yml'
"
```

Expected: every `cp` succeeds. Record `ENERGY_BACKUP_DIR` for rollback.

- [ ] **Step 6: Update the local gitignored EMHASS configuration**

Edit `emhass/share/config.json` in the isolated deployment worktree using the
same keys reviewed in `config.json.example`. This gitignored file is the source
for the live deployment but must never be staged or committed.

Confirm the rendered/live values match:

```text
sensor_power_load_no_var_loads = sensor.emhass_house_load_sample_w
var_model = sensor.emhass_house_load_sample_w
load_forecast_method = naive
sensor_linear_interp contains sampled load and actual PV
sensor_replace_zero contains actual PV but not sampled load
```

Never copy `config/site.env` or `secrets_emhass.yaml` into tracked files.

- [ ] **Step 7: Deploy the energy stack and reload EMHASS parameters**

Run:

```bash
./scripts/deploy-energy-stack.sh
```

Then restart EMHASS through the configured compose project so cached
`/data/params.pkl` cannot retain the tariff placeholder. Use paths from
`config/site.env`; do not hardcode a compose location.

Expected: `emhass`, `emhass-cron`, and `energy-monitor` are running.

- [ ] **Step 8: Run one manual MPC**

On the energy host:

```bash
docker compose exec emhass-cron \
  python3 /app/fetch_prices_emhass.py --action mpc -v
```

Expected:

- HA prices fetch successfully;
- weather cache validates or refreshes according to existing policy;
- EMHASS logs native naive load history retrieval;
- no `load_power_forecast`, `alpha`, `beta`, or 300 W mode is logged;
- optimization status is `Optimal`;
- publish succeeds;
- `last_enever_run.json` reports `mpc.ok=true`;
- `input_datetime.emhass_last_successful_mpc` advances only after publish.

- [ ] **Step 9: Verify a natural `:30` run**

Observe the next scheduled `:30` MPC without manually shifting its timing.

Expected:

- no `Forecast.get_mix_forecast` NaN traceback;
- sampled load history includes the current half-hour geometry;
- `mpc.ok=true`;
- control freshness remains on;
- master state is unchanged.

Also verify one natural `:00` run. These two boundaries cover the reproduced
race condition.

- [ ] **Step 10: Verify the fail-closed path without disrupting live control**

Use tests or a dry-run/stubbed environment; do not intentionally break the live
HA connection while Remote EMS is controlling the battery.

Confirm from code and test evidence:

- an EMHASS optimization exception returns nonzero;
- failure status is written;
- `finalize_successful_mpc_run` is not called;
- the successful timestamp is not advanced;
- the existing HA freshness template becomes stale at 40 minutes;
- the existing stale-plan automation restores native control.

- [ ] **Step 11: Observe for 24 hours and update memory**

After at least 24 hours containing several `:00` and `:30` runs, record:

- count of successful and failed MPC runs;
- whether any NaN mix traceback occurred;
- sampled sensor history coverage;
- whether any stale-plan/native-control transition occurred;
- rollback backup path;
- deployment timestamps.

Update `memory-bank/activeContext.md` with the actual state. Add this invariant
to `memory-bank/techContext.md`:

```markdown
- Load forecast failure is fail-closed. EMHASS uses
  `sensor.emhass_house_load_sample_w` with native `naive`; cron must not send a
  load list or fabricate a constant/dynamic fallback.
```

Do not mark the work deployed or verified before the natural-boundary and
24-hour checks have actually completed.

## Rollback

If Task 4 fails after the energy-host cutover:

1. turn off the existing EMHASS master control through the normal operator
   control so Sigenergy returns to native operation;
2. restore the timestamped `config.json`, cron Python file, cron table, and
   `docker-compose.yml` from `ENERGY_BACKUP_DIR` to their corresponding paths
   under `ENERGY_REMOTE_DIR`;
3. rebuild `emhass-cron`, then force-recreate `emhass` and `emhass-cron` using
   the restored compose file so cached parameters and old cron code reload;
4. verify the successful-plan timestamp remains stale and Sigenergy remains in
   native control before considering re-enabling the master;
5. leave `sensor.emhass_house_load_sample_w` deployed for diagnosis;
6. record the failed gate and rollback evidence in `memory-bank/activeContext.md`.

Do not reintroduce 300 W or another automatic load fallback during rollback.
