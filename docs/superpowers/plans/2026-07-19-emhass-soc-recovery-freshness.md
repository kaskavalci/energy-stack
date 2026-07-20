# EMHASS SOC Recovery Freshness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Date:** 2026-07-20
**Author:** GPT-5.6 Sol

**Goal:** Restore below-floor recovery so a fresh EMHASS charge instruction latches capped charging until the configured recovery target, using a durable MPC-success marker instead of the unmaintained `sensor.emhass_mpc_status`.

**Architecture:** After `naive-mpc-optim` and `publish-data` both succeed, `emhass-cron` writes one completion timestamp to a source-controlled Home Assistant `input_datetime`. One HA template binary sensor owns all plan-freshness decisions. A small, restart-restored Sigenergy recovery state machine waits for a fresh negative battery command below the SOC floor, stores the capped charge setpoint, reconciles it until the recovery target, then immediately returns control to current MPC or native self-consumption.

**Tech Stack:** Python 3 standard library (`urllib`, `unittest`), Home Assistant YAML packages and Jinja templates, Sigenergy MQTT entities, shell deployment scripts.

## Global Constraints

- `optimization_time_step` remains **30** minutes and `prediction_horizon` remains **32** maximum.
- Price methods remain **csv**; weather remains **open-meteo**; `open_meteo_cache_max_age` remains **120** minutes.
- A price-fetch failure still aborts MPC; there is no stale-CSV fallback.
- Five-minute `publish-data` calls must never advance the successful-MPC timestamp.
- A new recovery starts only from a fresh EMHASS charge command below the configured SOC floor.
- Once started, recovery remains active until the configured recovery target, unless master-off or invalid SOC cancels it.
- Every charge limit remains clamped to the operator cap and the device-advertised maximum.
- Once recovery is active, always write the clamped charge limit including zero (pause charging). Do not cancel recovery when the power threshold rises or a cap drops to zero. Only master-off, invalid SOC, or reaching the recovery target may clear recovery. The start path still requires a charge command above the power threshold.
- Recovery may start only when SOC parses and is in the inclusive range `0..100`; completion likewise requires valid SOC in `0..100` at or above the target.
- Normal EMHASS control applies on successful-MPC marker changes and when control becomes allowed. Its five-minute fallback retry is suppressed only at minute `0` and `30` to avoid racing the MPC job; there is no direct half-hour normal-apply trigger.
- Master-off always restores Sigenergy native `Maximum Self-consumption (Default)`.
- Load/PV history placeholder migration and recorder repair are out of scope.
- Read `config/site.env` before SSH, deployment, or live checks; never hardcode hostnames, IPs, usernames, or the plant slug.
- Preserve all unrelated dirty-worktree changes. In particular, do not edit `README.md`, `home-assistant/dashboards/emhass_trading.yaml`, `scripts/tests/test_emhass_trading_dashboard.py`, or the existing changed section of `memory-bank/activeContext.md`.
- Do not create git commits unless the user explicitly authorizes them.

## File map

- `emhass-cron/fetch_prices_emhass.py`: publish the durable successful-MPC timestamp after the complete MPC chain.
- `emhass-cron/tests/test_fetch_prices_emhass.py`: unit-test HA API payload, retry behavior, and success/failure finalization.
- `home-assistant/ha_emhass_helpers.yaml`: define the restart-restored successful-MPC helper.
- `home-assistant/ha_emhass_dashboard.yaml`: define the single MPC-freshness sensor and make normal-control diagnostics consume it.
- `home-assistant/ha_sigen_ems.yaml`: define explicit recovery state/setpoint, start/reconcile scripts, and actuator precedence.
- `home-assistant/ha_emhass_monitoring.yaml`: make stale-plan and recovery-stalled alerts consume the new contract.
- `scripts/tests/test_sigen_ems_package.py`: structural regression tests covering the HA contract and recovery state machine.
- `emhass-cron/README.md`: document when the durable timestamp advances.
- `memory-bank/progress.md`: record the completed freshness and recovery work after live verification.

---

### Task 1: Home Assistant successful-MPC publisher

**Files:**
- Modify: `emhass-cron/tests/test_fetch_prices_emhass.py`
- Modify: `emhass-cron/fetch_prices_emhass.py:45-155`

**Interfaces:**
- Consumes: `emhass/secrets_emhass.yaml` keys `hass_url` and `long_lived_token`.
- Produces: `publish_ha_input_datetime(secrets_path: Path, entity_id: str, completed_at: datetime, *, timeout: float = 15, max_attempts: int = 3, retry_delay_s: float = 2.0) -> None`.
- Produces constants `MPC_SUCCESS_ENTITY_ID`, `MPC_STATUS_MAX_ATTEMPTS`, and `MPC_STATUS_RETRY_DELAY_S`.

- [ ] **Step 1: Add failing API-contract tests**

Add `call` and `MagicMock` to the existing `unittest.mock` import, then append this test class before `if __name__ == "__main__":`:

```python
class PublishHaInputDatetimeTests(unittest.TestCase):
    def setUp(self):
        self.secrets = Path("/tmp/test_publish_mpc_secrets.yaml")
        self.secrets.write_text(
            "hass_url: http://ha.local:8123\nlong_lived_token: test-token\n",
            encoding="utf-8",
        )
        self.completed_at = datetime.fromisoformat("2026-07-19T10:30:12+02:00")

    def tearDown(self):
        self.secrets.unlink(missing_ok=True)

    @staticmethod
    def response():
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"[]"
        return response

    @patch.object(fetch_prices_emhass, "urlopen")
    def test_posts_timestamp_to_input_datetime_service(self, mock_urlopen):
        mock_urlopen.return_value = self.response()

        fetch_prices_emhass.publish_ha_input_datetime(
            self.secrets,
            "input_datetime.emhass_last_successful_mpc",
            self.completed_at,
        )

        request = mock_urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "http://ha.local:8123/api/services/input_datetime/set_datetime",
        )
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {
                "entity_id": "input_datetime.emhass_last_successful_mpc",
                "timestamp": self.completed_at.timestamp(),
            },
        )

    @patch.object(fetch_prices_emhass.time, "sleep")
    @patch.object(fetch_prices_emhass, "urlopen")
    def test_retries_twice_with_same_timestamp(self, mock_urlopen, mock_sleep):
        mock_urlopen.side_effect = [
            URLError("first"),
            URLError("second"),
            self.response(),
        ]

        fetch_prices_emhass.publish_ha_input_datetime(
            self.secrets,
            "input_datetime.emhass_last_successful_mpc",
            self.completed_at,
        )

        self.assertEqual(mock_urlopen.call_count, 3)
        mock_sleep.assert_has_calls([call(2.0), call(2.0)])
        payloads = [
            json.loads(item.args[0].data.decode("utf-8"))
            for item in mock_urlopen.call_args_list
        ]
        self.assertEqual(payloads, [payloads[0], payloads[0], payloads[0]])

    @patch.object(fetch_prices_emhass.time, "sleep")
    @patch.object(fetch_prices_emhass, "urlopen")
    def test_raises_after_three_failed_attempts(self, mock_urlopen, mock_sleep):
        mock_urlopen.side_effect = URLError("HA unavailable")

        with self.assertRaisesRegex(
            RuntimeError, "Could not update input_datetime.emhass_last_successful_mpc"
        ):
            fetch_prices_emhass.publish_ha_input_datetime(
                self.secrets,
                "input_datetime.emhass_last_successful_mpc",
                self.completed_at,
            )

        self.assertEqual(mock_urlopen.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    def test_rejects_naive_completion_timestamp(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            fetch_prices_emhass.publish_ha_input_datetime(
                self.secrets,
                "input_datetime.emhass_last_successful_mpc",
                datetime(2026, 7, 19, 10, 30),
            )
```

Also add these imports at the top of the test file:

```python
from http.client import IncompleteRead
import json
from datetime import datetime
from unittest.mock import MagicMock, call, patch
from urllib.error import HTTPError, URLError
```

Add response-read coverage to the real publisher retry loop:

- First response `read()` raises `ConnectionResetError`, second response
  succeeds: require two `urlopen` calls, one sleep, and the same timestamp body.
- Every response `read()` raises `IncompleteRead`: require three attempts, two
  sleeps, and a final `RuntimeError` whose cause is `IncompleteRead`.

- [ ] **Step 2: Run the new tests and confirm the missing interface**

Run:

```bash
python3 -m unittest \
  emhass-cron/tests/test_fetch_prices_emhass.py \
  -v
```

Expected: the new tests error with `AttributeError` because `publish_ha_input_datetime` does not exist.

- [ ] **Step 3: Implement the authenticated, idempotent publisher**

Import `HTTPException` from `http.client` so response protocol failures are
covered by the bounded retry contract.

Add these constants below `WEATHER_CACHE_RETRY_BACKOFF_S`:

```python
MPC_SUCCESS_ENTITY_ID = "input_datetime.emhass_last_successful_mpc"
MPC_STATUS_MAX_ATTEMPTS = 3
MPC_STATUS_RETRY_DELAY_S = 2.0
```

Add this function immediately after `fetch_ha_state`:

```python
def publish_ha_input_datetime(
    secrets_path: Path,
    entity_id: str,
    completed_at: datetime,
    *,
    timeout: float = 15,
    max_attempts: int = MPC_STATUS_MAX_ATTEMPTS,
    retry_delay_s: float = MPC_STATUS_RETRY_DELAY_S,
) -> None:
    """Set a restart-restored HA input_datetime using one idempotent timestamp."""
    if completed_at.tzinfo is None or completed_at.utcoffset() is None:
        raise ValueError("completed_at must be timezone-aware")
    if not secrets_path.is_file():
        raise RuntimeError(f"Secrets file not found: {secrets_path}")
    text = secrets_path.read_text(encoding="utf-8")
    hass_url = _parse_simple_yaml_value(text, "hass_url")
    token = _parse_simple_yaml_value(text, "long_lived_token")
    if not hass_url or not token:
        raise RuntimeError("hass_url or long_lived_token missing in secrets")
    if not hass_url.endswith("/"):
        hass_url += "/"

    url = f"{hass_url}api/services/input_datetime/set_datetime"
    body = json.dumps(
        {"entity_id": entity_id, "timestamp": completed_at.timestamp()}
    ).encode("utf-8")
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        request = Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                response.read()
            return
        except (HTTPError, URLError, TimeoutError, OSError, HTTPException) as exc:
            last_exc = exc
            if attempt >= max_attempts:
                break
            logging.warning(
                "HA successful-MPC marker attempt %d/%d failed (%s); retry in %.0fs",
                attempt,
                max_attempts,
                exc,
                retry_delay_s,
            )
            time.sleep(retry_delay_s)

    raise RuntimeError(
        f"Could not update {entity_id} after {max_attempts} attempts: {last_exc}"
    ) from last_exc
```

- [ ] **Step 4: Run the focused tests**

Run the command from Step 2 again.

Expected: all existing and new `test_fetch_prices_emhass.py` tests pass,
including response-read I/O/protocol retries. Success is recognized only after
`response.read()` completes; exhausted retryable failures are normalized to a
`RuntimeError` chained from the final exception.

- [ ] **Step 5: Checkpoint without committing**

Run:

```bash
git diff --check -- \
  emhass-cron/fetch_prices_emhass.py \
  emhass-cron/tests/test_fetch_prices_emhass.py
```

Expected: no output. Do not run `git commit`.

---

### Task 2: Successful-run finalization and failure visibility

**Files:**
- Modify: `emhass-cron/tests/test_fetch_prices_emhass.py`
- Modify: `emhass-cron/fetch_prices_emhass.py:720-1065`
- Modify: `home-assistant/ha_emhass_helpers.yaml`

**Interfaces:**
- Consumes: `publish_ha_input_datetime(...)` from Task 1.
- Produces: `finalize_successful_mpc_run(secrets_path: Path, share_dir: Path, completed_at: datetime, *, prediction_horizon: int) -> bool`.
- Produces HA entity `input_datetime.emhass_last_successful_mpc` with date and time.

- [ ] **Step 1: Add failing finalization tests**

Append this class before the test module's `if __name__ == "__main__":` block:

```python
class FinalizeSuccessfulMpcRunTests(unittest.TestCase):
    def setUp(self):
        self.secrets = Path("/tmp/finalize_mpc_secrets.yaml")
        self.share = Path("/tmp/finalize_mpc_share")
        self.completed_at = datetime.fromisoformat("2026-07-19T11:00:03+02:00")

    @patch.object(fetch_prices_emhass, "write_run_status")
    @patch.object(fetch_prices_emhass, "publish_ha_input_datetime")
    def test_success_marker_precedes_ok_status(self, mock_publish, mock_status):
        parent = MagicMock()
        parent.attach_mock(mock_publish, "publish")
        parent.attach_mock(mock_status, "status")

        ok = fetch_prices_emhass.finalize_successful_mpc_run(
            self.secrets,
            self.share,
            self.completed_at,
            prediction_horizon=26,
        )

        self.assertTrue(ok)
        self.assertEqual(parent.mock_calls[0][0], "publish")
        mock_publish.assert_called_once_with(
            self.secrets,
            fetch_prices_emhass.MPC_SUCCESS_ENTITY_ID,
            self.completed_at,
        )
        mock_status.assert_any_call(
            self.share,
            "mpc",
            ok=True,
            metadata={
                "prediction_horizon": 26,
                "load_forecast_mode": "constant_300w",
                "load_forecast_constant_w": 300.0,
                "plan_generated_at": self.completed_at.isoformat(),
            },
        )
        mock_status.assert_any_call(self.share, "weather", ok=True)

    @patch.object(fetch_prices_emhass, "write_run_status")
    @patch.object(fetch_prices_emhass, "publish_ha_input_datetime")
    def test_marker_failure_records_pipeline_failure(self, mock_publish, mock_status):
        mock_publish.side_effect = RuntimeError("HA unavailable")

        ok = fetch_prices_emhass.finalize_successful_mpc_run(
            self.secrets,
            self.share,
            self.completed_at,
            prediction_horizon=26,
        )

        self.assertFalse(ok)
        mock_status.assert_called_once_with(
            self.share,
            "mpc",
            ok=False,
            error="HA successful-MPC marker failed: HA unavailable",
            failure_kind="status_publish",
        )
```

- [ ] **Step 2: Verify the finalization tests fail**

Run:

```bash
python3 -m unittest \
  emhass-cron/tests/test_fetch_prices_emhass.py \
  -v
```

Expected: both new tests error because `finalize_successful_mpc_run` is undefined.

- [ ] **Step 3: Implement finalization**

Add this function immediately after `run_mpc_chain`:

```python
def finalize_successful_mpc_run(
    secrets_path: Path,
    share_dir: Path,
    completed_at: datetime,
    *,
    prediction_horizon: int,
) -> bool:
    try:
        publish_ha_input_datetime(
            secrets_path,
            MPC_SUCCESS_ENTITY_ID,
            completed_at,
        )
    except RuntimeError as exc:
        error = f"HA successful-MPC marker failed: {exc}"
        logging.error(error)
        write_run_status(
            share_dir,
            "mpc",
            ok=False,
            error=error,
            failure_kind="status_publish",
        )
        return False

    write_run_status(
        share_dir,
        "mpc",
        ok=True,
        metadata={
            "prediction_horizon": prediction_horizon,
            "load_forecast_mode": LOAD_FORECAST_MODE,
            "load_forecast_constant_w": LOAD_FORECAST_CONSTANT_W,
            "plan_generated_at": completed_at.isoformat(),
        },
    )
    write_run_status(share_dir, "weather", ok=True)
    return True
```

At the successful end of `main()`'s `args.action == "mpc"` branch, replace the existing two `write_run_status(...)` calls and `return 0` with:

```python
        completed_at = datetime.now(ZoneInfo(args.timezone))
        finalized = finalize_successful_mpc_run(
            args.secrets,
            args.share_dir,
            completed_at,
            prediction_horizon=prediction_horizon,
        )
        return 0 if finalized else 1
```

This placement must remain after `run_mpc_chain(...)` returns, so weather validation, optimization, and `publish-data` have all succeeded. Do not call finalization from `--action publish`, `--dry-run`, `--write-only`, `--soc-refresh`, or `refresh-weather` paths.

- [ ] **Step 4: Define the restart-restored HA helper**

Append this domain to `home-assistant/ha_emhass_helpers.yaml` after the existing `input_number` block:

```yaml

input_datetime:
  emhass_last_successful_mpc:
    name: EMHASS last successful MPC
    has_date: true
    has_time: true
    icon: mdi:clock-check-outline
```

Do not add `initial`; Home Assistant must restore the last value after restart.

- [ ] **Step 5: Run tests and render helpers**

Run:

```bash
python3 -m unittest \
  emhass-cron/tests/test_fetch_prices_emhass.py \
  -v
./scripts/render-ha-packages.sh
rg -n "emhass_last_successful_mpc" \
  home-assistant/ha_emhass_helpers.yaml \
  .rendered/home-assistant/ha_emhass_helpers.yaml
```

Expected: tests pass; rendering succeeds; both source and rendered helper files contain the entity exactly once.

- [ ] **Step 6: Checkpoint without committing**

Run:

```bash
git diff --check -- \
  emhass-cron/fetch_prices_emhass.py \
  emhass-cron/tests/test_fetch_prices_emhass.py \
  home-assistant/ha_emhass_helpers.yaml
```

Expected: no output. Do not run `git commit`.

---

### Task 3: One Home Assistant freshness contract

**Files:**
- Modify: `scripts/tests/test_sigen_ems_package.py`
- Modify: `home-assistant/ha_emhass_dashboard.yaml:130-225`
- Modify: `home-assistant/ha_emhass_monitoring.yaml:35-58`

**Interfaces:**
- Consumes: `input_datetime.emhass_last_successful_mpc` from Task 2.
- Produces: `binary_sensor.emhass_mpc_fresh` with attributes `reason`, `age_min`, and `last_successful_mpc`.
- Produces: `binary_sensor.emhass_control_allowed.block_reason` values `master_off`, `soc_invalid`, `prices_stale`, `plan_missing`, `plan_stale`, `optim_not_optimal`, `soc_below_floor`, or `ok`.

- [ ] **Step 1: Add failing structural tests for the freshness contract**

Add these constants near `PACKAGE_PATH`:

```python
DASHBOARD_PACKAGE_PATH = REPO_ROOT / "home-assistant" / "ha_emhass_dashboard.yaml"
MONITORING_PACKAGE_PATH = REPO_ROOT / "home-assistant" / "ha_emhass_monitoring.yaml"
```

Append this class before the module's `if __name__ == "__main__":` block:

```python
class EmhassMpcFreshnessContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dashboard = DASHBOARD_PACKAGE_PATH.read_text(encoding="utf-8")
        self.monitoring = MONITORING_PACKAGE_PATH.read_text(encoding="utf-8")

    def test_source_controlled_freshness_uses_success_marker(self) -> None:
        fresh = self.dashboard.split(
            "      - name: EMHASS MPC fresh\n", 1
        )[1].split("      - name: EMHASS plan valid\n", 1)[0]
        self.assertIn("input_datetime.emhass_last_successful_mpc", fresh)
        self.assertIn("age_s >= -5 * 60", fresh)
        self.assertIn("age_s < 40 * 60", fresh)
        self.assertIn("sensor.p_batt_forecast", fresh)
        self.assertIn("sensor.optim_status", fresh)
        self.assertIn("reason:", fresh)

    def test_control_gate_consumes_central_freshness_reason(self) -> None:
        control = self.dashboard.split(
            "      - name: EMHASS control allowed\n", 1
        )[1]
        self.assertIn("binary_sensor.emhass_mpc_fresh", control)
        self.assertIn("fresh_reason", control)
        self.assertNotIn("states.sensor.p_batt_forecast.last_updated", control)

    def test_validity_and_stale_monitor_use_central_sensor(self) -> None:
        self.assertIn(
            'state: "{{ is_state(\'binary_sensor.emhass_mpc_fresh\', \'on\') }}"',
            self.dashboard,
        )
        stale = self.monitoring.split(
            "      - name: EMHASS fail plan stale\n", 1
        )[1].split("      - name: EMHASS fail PV sensor\n", 1)[0]
        self.assertIn("binary_sensor.emhass_mpc_fresh", stale)
        self.assertIn("plan_stale", stale)
        self.assertNotIn("p_batt_forecast.last_updated", stale)
```

In the existing
`EmhassPlanValidityPresentationTests.test_valid_plan_stays_positive_and_problem_is_separate`,
replace the final expected state with:

```python
        self.assertIn(
            'state: "{{ not is_state(\'binary_sensor.emhass_mpc_fresh\', \'on\') }}"',
            problem,
        )
```

- [ ] **Step 2: Run the HA structural tests and confirm failure**

Run:

```bash
python3 -m unittest scripts/tests/test_sigen_ems_package.py -v
```

Expected: the new tests fail because `EMHASS MPC fresh` and the central dependency do not exist.

- [ ] **Step 3: Define `binary_sensor.emhass_mpc_fresh`**

Insert this template binary sensor before `EMHASS plan valid` in `home-assistant/ha_emhass_dashboard.yaml`:

```yaml
      - name: EMHASS MPC fresh
        unique_id: emhass_mpc_fresh
        default_entity_id: binary_sensor.emhass_mpc_fresh
        device_class: running
        state: >
          {% set bad = ['unknown', 'unavailable', 'none', ''] %}
          {% set completed = states('input_datetime.emhass_last_successful_mpc') %}
          {% set completed_ts = as_timestamp(completed, 0) %}
          {% set age_s = as_timestamp(now()) - completed_ts %}
          {% set pb = states('sensor.p_batt_forecast') %}
          {{ completed_ts > 0
             and age_s >= -5 * 60
             and age_s < 40 * 60
             and pb not in bad
             and (pb | float(none)) is not none
             and is_state('sensor.optim_status', 'Optimal') }}
        attributes:
          reason: >
            {% set bad = ['unknown', 'unavailable', 'none', ''] %}
            {% set completed = states('input_datetime.emhass_last_successful_mpc') %}
            {% set completed_ts = as_timestamp(completed, 0) %}
            {% set age_s = as_timestamp(now()) - completed_ts %}
            {% set pb = states('sensor.p_batt_forecast') %}
            {% if pb in bad or (pb | float(none)) is none %}
              plan_missing
            {% elif completed_ts <= 0 or age_s < -5 * 60 or age_s >= 40 * 60 %}
              plan_stale
            {% elif not is_state('sensor.optim_status', 'Optimal') %}
              optim_not_optimal
            {% else %}
              ok
            {% endif %}
          age_min: >
            {% set completed_ts = as_timestamp(states('input_datetime.emhass_last_successful_mpc'), 0) %}
            {{ ((as_timestamp(now()) - completed_ts) / 60) | round(1)
               if completed_ts > 0 else none }}
          last_successful_mpc: "{{ states('input_datetime.emhass_last_successful_mpc') }}"
```

Replace the `state` of `EMHASS plan valid` and `EMHASS plan problem` with:

```yaml
        state: "{{ is_state('binary_sensor.emhass_mpc_fresh', 'on') }}"
```

and:

```yaml
        state: "{{ not is_state('binary_sensor.emhass_mpc_fresh', 'on') }}"
```

respectively.

- [ ] **Step 4: Make normal control consume the central reason**

In all three `EMHASS control allowed` templates (`state`, `block_reason`, and `block_action`), define:

```jinja2
{% set fresh = is_state('binary_sensor.emhass_mpc_fresh', 'on') %}
{% set fresh_reason = state_attr('binary_sensor.emhass_mpc_fresh', 'reason')
   | default('plan_stale', true) %}
```

In the state and `block_reason` decision chains, replace the existing direct
plan-missing, `p_batt_forecast.last_updated`, and `optim_status` branches with
this exact branch before the SOC-floor check:

```jinja2
{% elif not fresh %}
  {% set ns.reason = fresh_reason %}
```

For the `block_reason` attribute, which currently prints values directly rather than using a namespace, use:

```jinja2
{% elif not fresh %}
  {{ fresh_reason }}
```

For `block_action`, use this exact branch:

```jinja2
{% elif not fresh %}
  {% if fresh_reason == 'plan_missing' %}
    Wait for next MPC run or check energy host.
  {% elif fresh_reason == 'optim_not_optimal' %}
    Check EMHASS logs for solver infeasibility.
  {% else %}
    Check emhass-cron; last successful MPC is older than 40 minutes.
  {% endif %}
```

Replace the diagnostic attribute `p_batt_age_min` with:

```yaml
          last_successful_mpc: "{{ states('input_datetime.emhass_last_successful_mpc') }}"
          mpc_age_min: "{{ state_attr('binary_sensor.emhass_mpc_fresh', 'age_min') }}"
```

Do not leave any actuator decision based on `p_batt_forecast.last_updated`.

- [ ] **Step 5: Make the stale-plan monitor consume the same reason**

Replace `EMHASS fail plan stale` state with:

```yaml
        state: >
          {{ not is_state('binary_sensor.emhass_mpc_fresh', 'on')
             and state_attr('binary_sensor.emhass_mpc_fresh', 'reason') == 'plan_stale' }}
```

Change the A5 message to:

```yaml
          message: >-
            Action: Check emhass-cron on the energy host. Last successful MPC is
            older than 40 minutes; five-minute publish-data does not refresh it.
```

- [ ] **Step 6: Run focused tests and render**

Run:

```bash
python3 -m unittest scripts/tests/test_sigen_ems_package.py -v
./scripts/render-ha-packages.sh
rg -n "emhass_mpc_fresh|emhass_last_successful_mpc" \
  .rendered/home-assistant/ha_emhass_dashboard.yaml \
  .rendered/home-assistant/ha_emhass_monitoring.yaml
```

Expected: structural tests pass; rendering succeeds; rendered dashboard and monitoring packages use the new entities.

- [ ] **Step 7: Checkpoint without committing**

Run:

```bash
git diff --check -- \
  home-assistant/ha_emhass_dashboard.yaml \
  home-assistant/ha_emhass_monitoring.yaml \
  scripts/tests/test_sigen_ems_package.py
```

Expected: no output. Do not run `git commit`.

---

### Task 4: Restart-safe recovery state machine

**Files:**
- Modify: `scripts/tests/test_sigen_ems_package.py`
- Modify: `home-assistant/ha_sigen_ems.yaml:1-620`

**Interfaces:**
- Consumes: `binary_sensor.emhass_mpc_fresh`, `sensor.p_batt_forecast`, `sensor.emhass_recovery_target_soc`, `input_number.emhass_soc_min`, and existing Sigenergy scripts/entities.
- Produces: `input_boolean.emhass_recovery_active`.
- Produces: `input_number.emhass_recovery_charge_w`.
- Produces: `script.sigen_clear_recovery`, `script.sigen_start_recovery`, and `script.sigen_apply_active_recovery`.
- Replaces automations `emhass_recovery_charge` and `emhass_recovery_complete` with `emhass_recovery_start` and `emhass_recovery_reconcile`.

- [ ] **Step 1: Add failing structural recovery tests**

Append this test class before the module's `if __name__ == "__main__":` block:

```python
class EmhassRecoveryStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.package = package_text()

    def test_recovery_helpers_are_restart_restored(self) -> None:
        boolean = self.package.split("input_boolean:\n", 1)[1].split(
            "input_number:\n", 1
        )[0]
        self.assertIn("emhass_recovery_active:", boolean)
        self.assertNotIn("initial:", boolean.split("emhass_recovery_active:", 1)[1])
        number = self.package.split("  emhass_recovery_charge_w:\n", 1)[1].split(
            "  emhass_soc_target:\n", 1
        )[0]
        self.assertNotIn("initial:", number)

    def test_recovery_start_requires_floor_freshness_and_charge(self) -> None:
        start = automation_block(
            "emhass_recovery_start", "emhass_recovery_reconcile"
        )
        self.assertIn("binary_sensor.emhass_mpc_fresh", start)
        self.assertIn("soc is not none and 0 <= soc <= 100", start)
        self.assertIn("below: input_number.emhass_soc_min", start)
        self.assertIn("p < (0 - threshold)", start)
        self.assertIn("service: script.sigen_start_recovery", start)
        self.assertNotIn("sensor.emhass_mpc_status", start)

    def test_active_recovery_owns_actuator_until_target(self) -> None:
        reconcile = self.package.split(
            "  - id: emhass_recovery_reconcile\n", 1
        )[1]
        self.assertIn("minutes: \"/5\"", reconcile)
        self.assertIn("platform: homeassistant", reconcile)
        self.assertIn("id: reconnect", reconcile)
        self.assertIn("soc >= target", reconcile)
        self.assertIn("service: script.sigen_apply_active_recovery", reconcile)
        self.assertIn("service: script.sigen_clear_recovery", reconcile)
        self.assertIn("service: script.sigen_apply_emhass_plan", reconcile)
        self.assertIn("service: script.sigen_restore_native_control", reconcile)

    def test_active_recovery_always_applies_clamped_charge_limit(self) -> None:
        active = script_block(
            "sigen_apply_active_recovery", "sigen_apply_emhass_plan"
        )
        self.assertIn("service: script.sigen_apply_charge_limits", active)
        self.assertIn('watts: "{{ charge_limit_w }}"', active)
        self.assertNotIn("emhass_power_threshold_w", active)

    def test_reconcile_clears_only_for_master_invalid_soc_or_target(self) -> None:
        reconcile = self.package.split(
            "  - id: emhass_recovery_reconcile\n", 1
        )[1]
        self.assertNotIn("stored_w < threshold", reconcile)
        self.assertNotIn(
            "SOC recovery cancelled because its stored setpoint was invalid.",
            reconcile,
        )
        self.assertIn("emhass_auto_control_master", reconcile)
        self.assertIn("trigger.id == 'invalid_soc'", reconcile)
        self.assertIn("soc is not none and 0 <= soc <= 100 and soc >= target", reconcile)
        self.assertEqual(
            reconcile.count("service: script.sigen_clear_recovery"),
            3,
        )

    def test_ordinary_apply_and_failure_fallback_skip_active_recovery(self) -> None:
        scheduled = automation_block(
            "emhass_sigen_apply_plan_scheduled",
            "emhass_sigen_disable_on_stale_plan",
        )
        stale = automation_block(
            "emhass_sigen_disable_on_stale_plan",
            "emhass_sigen_disable_on_bad_optim",
        )
        bad_optim = automation_block(
            "emhass_sigen_disable_on_bad_optim",
            "emhass_soc_floor_cutout",
        )
        for block in (scheduled, stale, bad_optim):
            self.assertIn("input_boolean.emhass_recovery_active", block)
            self.assertIn('state: "off"', block)

    def test_master_off_clears_recovery_before_native_restore(self) -> None:
        kill = automation_block(
            "emhass_sigen_kill_switch_off",
            "emhass_sigen_enforce_master_off",
        )
        clear_pos = kill.index("service: script.sigen_clear_recovery")
        native_pos = kill.index("service: script.sigen_disable_remote_ems")
        self.assertLess(clear_pos, native_pos)
```

In the existing `SigenNativeRestoreTests.test_disable_wrapper_is_restartable`,
change the block boundary so the test still isolates only the wrapper after the
new scripts are inserted:

```python
        disable = script_block(
            "sigen_disable_remote_ems", "sigen_clear_recovery"
        )
```

- [ ] **Step 2: Verify recovery tests fail**

Run:

```bash
python3 -m unittest scripts/tests/test_sigen_ems_package.py -v
```

Expected: new tests fail because the recovery helpers, scripts, and new automation IDs do not exist.

- [ ] **Step 3: Add restart-restored recovery helpers**

Under the existing `input_boolean` domain, add:

```yaml
  emhass_recovery_active:
    name: EMHASS SOC recovery active
    icon: mdi:battery-sync
```

Under `input_number`, insert this block immediately before `emhass_soc_target`:

```yaml
  emhass_recovery_charge_w:
    name: EMHASS recovery charge setpoint
    min: 0
    max: 15000
    step: 50
    unit_of_measurement: W
    mode: box
```

Do not add `initial` to either helper; both must restore across HA restart.

- [ ] **Step 4: Add recovery scripts**

Insert these scripts between `sigen_disable_remote_ems` and `sigen_apply_emhass_plan`:

```yaml
  sigen_clear_recovery:
    alias: Sigen clear EMHASS SOC recovery
    mode: restart
    sequence:
      - service: input_boolean.turn_off
        target:
          entity_id: input_boolean.emhass_recovery_active
      - service: input_number.set_value
        target:
          entity_id: input_number.emhass_recovery_charge_w
        data:
          value: 0

  sigen_start_recovery:
    alias: Sigen start EMHASS SOC recovery
    mode: restart
    sequence:
      - variables:
          plan_charge_w: "{{ states('sensor.emhass_batt_charge_power_w') | float(0) }}"
          operator_cap_w: "{{ states('input_number.emhass_max_charge_cap_w') | float(0) }}"
          device_cap_w: >-
            {{ (state_attr('number.__SIGEN_PLANT_SLUG___plant_max_charging_limit', 'max')
                | float(0) * 1000) | round(0) }}
          charge_limit_w: >-
            {{ [plan_charge_w | float(0), operator_cap_w | float(0),
                device_cap_w | float(0)] | min | round(0) }}
      - condition: template
        value_template: >-
          {{ charge_limit_w | float(0)
             >= states('input_number.emhass_power_threshold_w') | float(500) }}
      - service: input_number.set_value
        target:
          entity_id: input_number.emhass_recovery_charge_w
        data:
          value: "{{ charge_limit_w }}"
      - service: input_boolean.turn_on
        target:
          entity_id: input_boolean.emhass_recovery_active
      - service: script.sigen_set_ems_mode
        data:
          mode: charge
      - service: script.sigen_apply_charge_limits
        data:
          watts: "{{ charge_limit_w }}"
      - service: logbook.log
        data:
          name: EMHASS EMS
          entity_id: sensor.__SIGEN_PLANT_SLUG___plant_battery_soc
          message: >-
            Fresh EMHASS charge command started SOC recovery at
            {{ charge_limit_w }} W until
            {{ states('sensor.emhass_recovery_target_soc') }}%.

  sigen_apply_active_recovery:
    alias: Sigen reconcile active EMHASS SOC recovery
    mode: restart
    sequence:
      - variables:
          stored_w: "{{ states('input_number.emhass_recovery_charge_w') | float(0) }}"
          operator_cap_w: "{{ states('input_number.emhass_max_charge_cap_w') | float(0) }}"
          device_cap_w: >-
            {{ (state_attr('number.__SIGEN_PLANT_SLUG___plant_max_charging_limit', 'max')
                | float(0) * 1000) | round(0) }}
          charge_limit_w: >-
            {{ [stored_w | float(0), operator_cap_w | float(0),
                device_cap_w | float(0)] | min | round(0) }}
      - service: script.sigen_set_ems_mode
        data:
          mode: charge
      - service: script.sigen_apply_charge_limits
        data:
          watts: "{{ charge_limit_w }}"
```

- [ ] **Step 5: Establish actuator precedence**

Change `emhass_sigen_kill_switch_off` action to:

```yaml
    action:
      - service: script.sigen_clear_recovery
      - service: script.sigen_disable_remote_ems
```

Update `emhass_sigen_apply_plan_scheduled` in place so normal control is driven
by actionable events rather than a direct half-hour clock trigger:

```yaml
    trigger:
      - id: plan_completed
        platform: state
        entity_id: input_datetime.emhass_last_successful_mpc
      - id: control_enabled
        platform: state
        entity_id: binary_sensor.emhass_control_allowed
        to: "on"
      - id: retry
        platform: time_pattern
        minutes: "/5"
```

Remove any normal-apply `minutes: "/30"` trigger. Add a trigger-aware condition
that permits marker/control events at every minute but rejects only retry events
when `now().minute` is `0` or `30`. Keep one shared write limiter and make it
inclusive (`elapsed >= 300`). Structural tests must verify all three trigger
fragments, the boundary guard, absence of `/30`, and the existing
control-allowed/recovery-inactive gates.

Add this condition to each of `emhass_sigen_apply_plan_scheduled`, `emhass_sigen_disable_on_stale_plan`, and `emhass_sigen_disable_on_bad_optim`, immediately after their master/control conditions:

```yaml
      - condition: state
        entity_id: input_boolean.emhass_recovery_active
        state: "off"
```

This makes active recovery the actuator owner. Do not add the condition to the master-off automation or SOC-floor cutout. Threshold or cap changes may clamp
the active command to zero, but must not clear this latch.

- [ ] **Step 6: Replace the old recovery automations**

Replace both `emhass_recovery_charge` and `emhass_recovery_complete` with:

```yaml
  - id: emhass_recovery_start
    alias: "EMHASS recovery — latch fresh charge plan below SOC floor"
    description: >-
      Wait below the SOC floor for a fresh EMHASS charge instruction, then store
      its capped setpoint and keep recovery active until the configured target.
    mode: single
    trigger:
      - id: mpc_fresh
        platform: state
        entity_id: binary_sensor.emhass_mpc_fresh
        to: "on"
      - id: plan_changed
        platform: state
        entity_id: sensor.p_batt_forecast
      - id: floor_crossed
        platform: numeric_state
        entity_id: sensor.__SIGEN_PLANT_SLUG___plant_battery_soc
        below: input_number.emhass_soc_min
      - id: retry
        platform: time_pattern
        minutes: "/5"
    condition:
      - condition: state
        entity_id: input_boolean.emhass_auto_control_master
        state: "on"
      - condition: state
        entity_id: input_boolean.emhass_recovery_active
        state: "off"
      - condition: template
        value_template: >-
          {% set soc = states('sensor.__SIGEN_PLANT_SLUG___plant_battery_soc') | float(none) %}
          {{ soc is not none and 0 <= soc <= 100 }}
      - condition: numeric_state
        entity_id: sensor.__SIGEN_PLANT_SLUG___plant_battery_soc
        below: input_number.emhass_soc_min
      - condition: state
        entity_id: binary_sensor.emhass_mpc_fresh
        state: "on"
      - condition: template
        value_template: >-
          {% set p = states('sensor.p_batt_forecast') | float(0) %}
          {% set threshold = states('input_number.emhass_power_threshold_w') | float(500) %}
          {{ p < (0 - threshold) }}
      - condition: template
        value_template: >-
          {{ (state_attr('number.__SIGEN_PLANT_SLUG___plant_max_charging_limit', 'max')
              | float(0)) > 0 }}
      - condition: template
        value_template: >-
          {% set last = states('input_datetime.emhass_last_limit_write') %}
          {{ last in ['unknown', 'unavailable', '']
             or as_timestamp(now()) - as_timestamp(last) >= 300 }}
    action:
      - service: script.sigen_start_recovery

  - id: emhass_recovery_reconcile
    alias: "EMHASS recovery — reconcile active charge until target"
    description: >-
      Reapply a stored recovery command after reconnects, stop at the configured
      target, and cancel safely on master-off or sustained invalid SOC.
    mode: restart
    trigger:
      - id: periodic
        platform: time_pattern
        minutes: "/5"
      - id: target_reached
        platform: template
        value_template: >-
          {% set soc = states('sensor.__SIGEN_PLANT_SLUG___plant_battery_soc') | float(none) %}
          {% set target = states('sensor.emhass_recovery_target_soc') | float(none) %}
          {{ soc is not none and target is not none
             and 0 <= soc <= 100 and soc >= target }}
      - id: target_changed
        platform: state
        entity_id: sensor.emhass_recovery_target_soc
      - id: master_changed
        platform: state
        entity_id: input_boolean.emhass_auto_control_master
      - id: invalid_soc
        platform: template
        value_template: >-
          {% set soc = states('sensor.__SIGEN_PLANT_SLUG___plant_battery_soc') | float(none) %}
          {{ soc is none or soc < 0 or soc > 100 }}
        for: "00:02:00"
      - id: reconnect
        platform: state
        entity_id: select.__SIGEN_PLANT_SLUG___plant_remote_ems_control_mode
        from:
          - "unknown"
          - "unavailable"
      - id: ha_start
        platform: homeassistant
        event: start
    condition:
      - condition: state
        entity_id: input_boolean.emhass_recovery_active
        state: "on"
    action:
      - variables:
          soc: "{{ states('sensor.__SIGEN_PLANT_SLUG___plant_battery_soc') | float(none) }}"
          target: "{{ states('sensor.emhass_recovery_target_soc') | float(100) }}"
      - choose:
          - conditions:
              - condition: state
                entity_id: input_boolean.emhass_auto_control_master
                state: "off"
            sequence:
              - service: script.sigen_clear_recovery
              - service: script.sigen_restore_native_control
              - service: logbook.log
                data:
                  name: EMHASS EMS
                  message: "SOC recovery cancelled because master control is off."
          - conditions:
              - condition: template
                value_template: "{{ trigger.id == 'invalid_soc' }}"
            sequence:
              - service: script.sigen_clear_recovery
              - service: script.sigen_restore_native_control
              - service: logbook.log
                data:
                  name: EMHASS EMS
                  message: "SOC recovery cancelled because SOC was invalid for two minutes."
          - conditions:
              - condition: template
                value_template: >-
                  {{ soc is not none and 0 <= soc <= 100 and soc >= target }}
            sequence:
              - service: script.sigen_clear_recovery
              - choose:
                  - conditions:
                      - condition: state
                        entity_id: binary_sensor.emhass_control_allowed
                        state: "on"
                    sequence:
                      - service: script.sigen_apply_emhass_plan
                default:
                  - service: script.sigen_restore_native_control
              - service: logbook.log
                data:
                  name: EMHASS EMS
                  entity_id: sensor.__SIGEN_PLANT_SLUG___plant_battery_soc
                  message: >-
                    SOC {{ soc }}% reached recovery target {{ target }}%; recovery
                    cleared and normal control reconciled immediately.
        default:
          - condition: template
            value_template: >-
              {% set last = states('input_datetime.emhass_last_limit_write') %}
              {{ trigger.id in ['reconnect', 'ha_start']
                 or last in ['unknown', 'unavailable', '']
                 or as_timestamp(now()) - as_timestamp(last) >= 300 }}
          - condition: template
            value_template: "{{ soc is not none and 0 <= soc < target }}"
          - service: script.sigen_apply_active_recovery
```

The target branch deliberately clears recovery before reading/applying
`binary_sensor.emhass_control_allowed`; this allows normal MPC to own the
actuator again. If the plan is stale at completion, native self-consumption is
selected.

- [ ] **Step 7: Run focused tests and render**

Run:

```bash
python3 -m unittest scripts/tests/test_sigen_ems_package.py -v
./scripts/render-ha-packages.sh
rg -n "emhass_recovery_(active|charge_w|start|reconcile)|emhass_mpc_status" \
  home-assistant/ha_sigen_ems.yaml \
  .rendered/home-assistant/ha_sigen_ems.yaml
```

Expected: tests pass; rendering succeeds; recovery entities and automations are present; `sensor.emhass_mpc_status` has zero matches in both source and rendered Sigenergy packages.

- [ ] **Step 8: Checkpoint without committing**

Run:

```bash
git diff --check -- \
  home-assistant/ha_sigen_ems.yaml \
  scripts/tests/test_sigen_ems_package.py
```

Expected: no output. Do not run `git commit`.

---

### Task 5: Monitoring and operator documentation

**Files:**
- Modify: `scripts/tests/test_sigen_ems_package.py`
- Modify: `home-assistant/ha_emhass_monitoring.yaml:270-315`
- Modify: `emhass-cron/README.md`
- Modify after live verification: `memory-bank/progress.md`

**Interfaces:**
- Consumes: `input_boolean.emhass_recovery_active` and `binary_sensor.emhass_mpc_fresh`.
- Produces: D3 alert semantics distinguishing an active recovery stall from waiting below-floor for a charge command.

- [ ] **Step 1: Add failing monitoring assertions**

Add this test method to `EmhassRecoveryStateMachineTests`:

```python
    def test_recovery_stalled_monitor_observes_explicit_state(self) -> None:
        monitoring = MONITORING_PACKAGE_PATH.read_text(encoding="utf-8")
        stalled = monitoring.split(
            "  - id: emhass_notify_D3_recovery_stalled\n", 1
        )[1]
        self.assertIn("input_boolean.emhass_recovery_active", stalled)
        self.assertIn("id: active_recovery", stalled)
        self.assertIn("id: waiting_below_floor", stalled)
```

- [ ] **Step 2: Confirm the new monitoring test fails**

Run:

```bash
python3 -m unittest scripts/tests/test_sigen_ems_package.py -v
```

Expected: the new method fails because D3 does not reference the recovery-active helper.

- [ ] **Step 3: Split D3 into explicit active and waiting triggers**

Replace the D3 trigger and message with:

```yaml
    trigger:
      - id: active_recovery
        platform: state
        entity_id: input_boolean.emhass_recovery_active
        to: "on"
        for:
          hours: 2
      - id: waiting_below_floor
        platform: numeric_state
        entity_id: sensor.__SIGEN_PLANT_SLUG___plant_battery_soc
        below: input_number.emhass_soc_min
        for:
          hours: 6
    condition:
      - condition: state
        entity_id: input_boolean.emhass_auto_control_master
        state: "on"
    action:
      - service: script.emhass_push_alert
        data:
          title: "EMHASS D3: recovery stalled"
          message: >-
            {% if trigger.id == 'active_recovery' %}
              Action: Check Sigenergy mode, limits, and SOC. An authorized recovery
              charge has been active for more than two hours without reaching target.
            {% else %}
              Action: Check MPC plans and prices. SOC has remained below floor for six
              hours without a qualifying fresh EMHASS charge command.
            {% endif %}
```

- [ ] **Step 4: Document the cross-host timestamp contract**

Append this section to `emhass-cron/README.md`:

```markdown

## MPC success marker

After prices, weather validation, `naive-mpc-optim`, and `publish-data` all
succeed, the cron writes the same completion timestamp to:

- `emhass/share/last_enever_run.json` (`mpc.plan_generated_at`)
- Home Assistant `input_datetime.emhass_last_successful_mpc`

Home Assistant uses the helper—not `sensor.p_batt_forecast.last_updated`—for
the 40-minute actuator freshness gate. The five-minute publish-only automation
does not update it. If the HA helper update fails after three attempts, the cron
records `mpc.ok=false` with `failure_kind=status_publish`.
```

- [ ] **Step 5: Run focused tests and inspect source scope**

Run:

```bash
python3 -m unittest scripts/tests/test_sigen_ems_package.py -v
git diff --check -- \
  home-assistant/ha_emhass_monitoring.yaml \
  emhass-cron/README.md \
  scripts/tests/test_sigen_ems_package.py
git status --short
```

Expected: tests pass; diff check is silent; pre-existing unrelated dirty files remain present and unchanged. Do not run `git commit`.

---

### Task 6: Repository verification

**Files:**
- Verify all modified files from Tasks 1-5.
- Do not edit generated `.rendered/` files.

**Interfaces:**
- Consumes: all source and test changes.
- Produces: a locally verified deploy candidate with no source dependency on `sensor.emhass_mpc_status`.

- [ ] **Step 1: Run both Python test suites**

Run:

```bash
python3 -m unittest discover -s emhass-cron/tests -p 'test_*.py' -v
python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v
```

Expected: all tests pass.

- [ ] **Step 2: Render all Home Assistant packages**

Run:

```bash
./scripts/render-ha-packages.sh
```

Expected: `Rendered HA packages -> .../.rendered/home-assistant` and exit code 0.

- [ ] **Step 3: Assert the actuator contract structurally**

Run:

```bash
rg -n "sensor\.emhass_mpc_status" \
  home-assistant/ha_sigen_ems.yaml \
  home-assistant/ha_emhass_dashboard.yaml \
  home-assistant/ha_emhass_monitoring.yaml
rg -n "p_batt_forecast\.last_updated" \
  home-assistant/ha_sigen_ems.yaml \
  home-assistant/ha_emhass_dashboard.yaml \
  home-assistant/ha_emhass_monitoring.yaml || true
rg -n "emhass_last_successful_mpc|emhass_mpc_fresh|emhass_recovery_active" \
  home-assistant/ha_emhass_helpers.yaml \
  home-assistant/ha_emhass_dashboard.yaml \
  home-assistant/ha_sigen_ems.yaml \
  home-assistant/ha_emhass_monitoring.yaml
```

Expected: the first command has no matches. The second may show the existing
display-only `plan_updated_min` attribute in `ha_emhass_dashboard.yaml`, but no
match in `ha_sigen_ems.yaml`, the `EMHASS control allowed` block, or
`ha_emhass_monitoring.yaml`. The third command shows the helper, freshness
sensor, and recovery-state consumers.

- [ ] **Step 4: Review the exact scoped diff**

Run:

```bash
git diff --check
git diff -- \
  emhass-cron/fetch_prices_emhass.py \
  emhass-cron/tests/test_fetch_prices_emhass.py \
  home-assistant/ha_emhass_helpers.yaml \
  home-assistant/ha_emhass_dashboard.yaml \
  home-assistant/ha_sigen_ems.yaml \
  home-assistant/ha_emhass_monitoring.yaml \
  scripts/tests/test_sigen_ems_package.py \
  emhass-cron/README.md
```

Expected: `git diff --check` is silent. The scoped diff contains only the approved MPC marker, freshness, recovery, monitoring, tests, and documentation. Do not stage or commit.

---

### Task 7: Safe deployment and live verification

**Files:**
- Read: `config/site.env`
- Deploy via: `scripts/deploy-energy-stack.sh`
- Deploy via: `scripts/deploy-home-assistant.sh`
- Modify after successful live verification: `memory-bank/progress.md`

**Interfaces:**
- Consumes: verified repository candidate from Task 6 and role names from `config/site.env`.
- Produces: live durable timestamp, fresh-plan sensor, and recovery state machine while leaving master control off until explicit operator approval.

- [ ] **Step 1: Load the live role configuration**

Run:

```bash
source scripts/lib/source-site-env.sh
test -n "$ENERGY_REMOTE_HOST"
test -n "$ENERGY_REMOTE_DIR"
test -n "$HA_SSH_HOST"
test -n "$HA_SSH_PORT"
test -n "$SIGEN_PLANT_SLUG"
```

Expected: all commands return 0 without printing secrets.

- [ ] **Step 2: Confirm master-off before partial deployment**

In Home Assistant, verify:

```text
input_boolean.emhass_auto_control_master = off
Sigenergy mode = Maximum Self-consumption (Default)
```

If either condition is false, stop. Turn the master off through Home Assistant and wait for native mode confirmation before continuing.

- [ ] **Step 3: Create recoverable pre-deployment backups**

Run:

```bash
RECOVERY_DEPLOY_STAMP="$(date +%Y%m%d-%H%M%S)"
ENERGY_RECOVERY_BACKUP="${ENERGY_REMOTE_DIR}/backups/emhass-recovery-${RECOVERY_DEPLOY_STAMP}"
HA_RECOVERY_BACKUP="/mnt/data/supervisor/homeassistant/backups/emhass-recovery-${RECOVERY_DEPLOY_STAMP}"

ssh "$ENERGY_REMOTE_HOST" \
  "mkdir -p '$ENERGY_RECOVERY_BACKUP' && \
   cp '$ENERGY_REMOTE_DIR/emhass-cron/fetch_prices_emhass.py' '$ENERGY_RECOVERY_BACKUP/'"

ssh -p "$HA_SSH_PORT" "$HA_SSH_USER@$HA_SSH_HOST" \
  "mkdir -p '$HA_RECOVERY_BACKUP' && \
   cp /mnt/data/supervisor/homeassistant/ha_emhass_helpers.yaml '$HA_RECOVERY_BACKUP/' && \
   cp /mnt/data/supervisor/homeassistant/ha_emhass_dashboard.yaml '$HA_RECOVERY_BACKUP/' && \
   cp /mnt/data/supervisor/homeassistant/ha_emhass_monitoring.yaml '$HA_RECOVERY_BACKUP/' && \
   cp /mnt/data/supervisor/homeassistant/ha_sigen_ems.yaml '$HA_RECOVERY_BACKUP/'"

printf '%s\n' "$ENERGY_RECOVERY_BACKUP" "$HA_RECOVERY_BACKUP"
```

Expected: both explicit backup directories are printed and contain the prior
producer/consumer files. Keep these variable values in the executing shell.

If any later live check fails, keep the master off and roll back with:

```bash
ssh "$ENERGY_REMOTE_HOST" \
  "cp '$ENERGY_RECOVERY_BACKUP/fetch_prices_emhass.py' \
      '$ENERGY_REMOTE_DIR/emhass-cron/fetch_prices_emhass.py' && \
   cd '$ENERGY_REMOTE_DIR' && \
   docker compose build emhass-cron && \
   docker compose up -d emhass-cron"

ssh -p "$HA_SSH_PORT" "$HA_SSH_USER@$HA_SSH_HOST" \
  "cp '$HA_RECOVERY_BACKUP/ha_emhass_helpers.yaml' /mnt/data/supervisor/homeassistant/ && \
   cp '$HA_RECOVERY_BACKUP/ha_emhass_dashboard.yaml' /mnt/data/supervisor/homeassistant/ && \
   cp '$HA_RECOVERY_BACKUP/ha_emhass_monitoring.yaml' /mnt/data/supervisor/homeassistant/ && \
   cp '$HA_RECOVERY_BACKUP/ha_sigen_ems.yaml' /mnt/data/supervisor/homeassistant/ && \
   ha core check && ha core restart"
```

Expected rollback state: master remains off, HA configuration validates, HA
restarts with the previous packages, and Sigenergy native self-consumption owns
the plant.

- [ ] **Step 4: Deploy the energy host producer**

Run:

```bash
./scripts/deploy-energy-stack.sh
```

Expected: location validation passes; `emhass-cron` and `energy-monitor` rebuild; containers start. A cron run before the HA helper exists may report `failure_kind=status_publish`; that is safe while master is off.

- [ ] **Step 5: Deploy the Home Assistant consumer**

Run:

```bash
./scripts/deploy-home-assistant.sh
```

Expected: render succeeds; `ha core check` passes; backup path is printed; HA restarts. Reconfirm master remains off and Sigenergy returns to native self-consumption.

- [ ] **Step 6: Run one manual MPC after both sides are deployed**

Run:

```bash
ssh "$ENERGY_REMOTE_HOST" \
  "cd '$ENERGY_REMOTE_DIR' && docker compose exec -T emhass-cron \
   python3 /app/fetch_prices_emhass.py --action mpc -v"
```

Expected: command exits 0 after price fetch, weather validation, `naive-mpc-optim`, `publish-data`, and successful marker publication.

- [ ] **Step 7: Verify local and HA timestamps agree**

Run:

```bash
ssh "$ENERGY_REMOTE_HOST" \
  "cd '$ENERGY_REMOTE_DIR' && docker compose exec -T emhass-cron \
   python3 -c \"import json, os; from pathlib import Path; \
from fetch_prices_emhass import fetch_ha_state; \
s=Path(os.environ['EMAHASS_SECRETS']); \
print(json.dumps({'local': json.loads(Path('/share/last_enever_run.json').read_text())['mpc'], \
'ha': fetch_ha_state(s, 'input_datetime.emhass_last_successful_mpc'), \
'fresh': fetch_ha_state(s, 'binary_sensor.emhass_mpc_fresh'), \
'recovery': fetch_ha_state(s, 'input_boolean.emhass_recovery_active')}, indent=2))\""
```

Expected:

```text
local.ok = true
local.plan_generated_at = current run completion
ha.state = the same completion time, allowing HA display formatting
fresh.state = on
fresh.attributes.reason = ok
recovery.state = off
```

- [ ] **Step 8: Verify marker aging cannot be refreshed by publish-only**

Record the helper state from Step 7, invoke publish-only, then read it again:

```bash
ssh "$ENERGY_REMOTE_HOST" \
  "cd '$ENERGY_REMOTE_DIR' && docker compose exec -T emhass-cron \
   python3 /app/fetch_prices_emhass.py --action publish"
```

Expected: `input_datetime.emhass_last_successful_mpc` remains exactly unchanged while forecast entities may update.

- [ ] **Step 9: Verify the 40-minute expiry while master is off**

In Home Assistant Developer Tools → Actions, run:

```yaml
action: input_datetime.set_datetime
target:
  entity_id: input_datetime.emhass_last_successful_mpc
data:
  timestamp: "{{ (now() - timedelta(minutes=45)).timestamp() }}"
```

Expected within one template update: `binary_sensor.emhass_mpc_fresh = off` and
its `reason = plan_stale`. Invoke the publish-only command from Step 8 and
confirm the marker stays old and freshness stays off. Then rerun the manual MPC
from Step 6 and confirm the marker returns to the real completion time,
freshness returns to `on`, and `reason = ok`.

- [ ] **Step 10: Validate recovery templates without falsifying SOC**

With master still off, use Home Assistant Developer Tools → Template to evaluate:

```jinja2
{{ {
  'fresh': states('binary_sensor.emhass_mpc_fresh'),
  'fresh_reason': state_attr('binary_sensor.emhass_mpc_fresh', 'reason'),
  'soc': states('sensor.__SIGEN_PLANT_SLUG___plant_battery_soc'),
  'floor': states('input_number.emhass_soc_min'),
  'target': states('sensor.emhass_recovery_target_soc'),
  'p_batt': states('sensor.p_batt_forecast'),
  'recovery_active': states('input_boolean.emhass_recovery_active'),
  'recovery_w': states('input_number.emhass_recovery_charge_w')
} }}
```

Replace `__SIGEN_PLANT_SLUG__` with the rendered entity shown by `config/site.env`; do not put the live slug into tracked source. Expected: every entity resolves, recovery is off, target equals configured floor plus 5 percentage points capped at 100, and no value is `unknown` or `unavailable` except a never-started recovery setpoint may initially be `unknown` before helper restoration completes.

- [ ] **Step 11: Observe two cron boundaries with master off**

Wait for two normal `:00`/`:30` runs. After each, repeat Step 7.

Expected: each successful MPC advances both timestamps; freshness stays on; recovery remains off; Sigenergy remains native because master is off.

- [ ] **Step 12: Obtain explicit approval before re-enabling master**

Do not toggle `input_boolean.emhass_auto_control_master` during automated deployment. Report the verified state to the user and request explicit approval to turn it on. If approved, enable it in Home Assistant and verify normal plan application at the next boundary. If SOC is not genuinely below the floor, do not fake SOC or force a recovery event; rely on structural tests, template evaluation, and future real-event traces.

- [ ] **Step 13: Update the memory bank after verified deployment**

In `memory-bank/progress.md`:

- Add a completed item dated `2026-07-19` for durable MPC freshness and latched SOC recovery.
- Mark **`emhass_recovery_complete` guard** complete.
- Mark **Freshness gating before EMS apply (`emhass_control_allowed`)** complete.
- Record that load/PV history placeholders remain pending until full-day history HTTP 200.

Preserve the rest of the file and do not edit the unrelated dirty section of `memory-bank/activeContext.md`.

- [ ] **Step 14: Final verification without committing**

Run:

```bash
python3 -m unittest discover -s emhass-cron/tests -p 'test_*.py' -v
python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v
./scripts/render-ha-packages.sh
git diff --check
git status --short
```

Expected: both suites pass, rendering succeeds, diff check is silent, only scoped source/test/docs plus pre-existing user changes are modified, and no commit has been created.
