# EMHASS Load Quality and Envoy-Lag Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make EMHASS load history truthful and resilient to the site's known Envoy reporting delay, while keeping MPC fail-closed when the estimate is not trustworthy.

**Architecture:** Home Assistant will own 1-minute sample estimation. The existing trigger-based `sensor.emhass_house_load_sample_w` will evaluate source health, calculate raw balance, classify quality, and hold the last valid estimate for at most 5 minutes using `variables:` under trigger and `this.attributes.last_valid_at`. The cron client will request all history records with `significant_changes_only=0` and attributes, validate `sampled_at` and `load_quality`, and retry one failed load validation after 90 seconds. Envoy input gating will no longer depend on MPC output freshness. The numeric sensor state will remain available for history; its quality will decide if cron may use it.

**Tech Stack:** Home Assistant YAML/Jinja template sensors, Python 3 standard library, `unittest`, `ruamel.yaml` contract tests, HA `/api/template` evaluation via `scripts/lib/ha_template_eval.py`, and the existing `last_enever_run.json` status writer.

**Spec:** [docs/superpowers/specs/2026-09-02-emhass-load-quality-design.md](../specs/2026-09-02-emhass-load-quality-design.md)

---

## Global Constraints

- Keep `optimization_time_step=30`, `prediction_horizon <= 32`, and the existing no-stale-data MPC policy.
- Keep price failure and weather failure policies unchanged. Do not add stale CSV fallback, zero-fill, or old-plan publication.
- The 5-minute held-estimate limit is intentional. A 5–10 minute Envoy value lag is normal, but a held estimate older than 5 minutes is invalid.
- A lag at an ordinary time should normally recover before the next `:00` or `:30` cron boundary. A mismatch that spans approximately `:23` through `:32` may fail closed. Document this as an accepted safety boundary and test it.
- Use these exact quality values: `valid`, `valid_noise_clamp`, `held_estimate`, and `invalid`.
- Keep the load state numeric in all quality states. At boot, when there is no previous valid value, use numeric `0` with `load_quality=invalid`; cron must reject it.
- In `load_history` status metadata, `mode` must remain strictly the 30-minute bin coverage mode (`healthy`, `degraded`, `unsafe`). If all 32 bins are covered, `mode` is `healthy`. The lag state belongs exclusively in `quality: "held_estimate"` and `quality_reason: "envoy_update_lag"`. Do not set `mode="degraded"` for held estimates; doing so breaks `monitor/health_notify.py` and causes false B6 phone alerts.
- In `fetch_prices_emhass.py`, if the newest sample is within 5 minutes but has `load_quality == 'invalid'`, fail with a quality error (`Load history sample invalid: {reason}`) rather than masking it as "sample is stale".
- Put the balance evaluation, 5-minute hold timer, and `load_quality` attribute directly in the existing triggered `sensor.emhass_house_load_sample_w` using a `variables:` block under trigger to prevent duplicating Jinja logic across attributes.
- The stateful template must handle an undefined or non-numeric `this.state` and a missing `this.attributes.last_valid_at` without a template error.
- At night or when `binary_sensor.emhass_envoy_required` is `off`, exclude Envoy from `source_spread_s` and transport freshness checks. Only check P1 and Sigen.
- Use `attributes.sampled_at` as the primary sample timestamp. A compatibility fallback to `last_updated` is allowed only for records with usable quality metadata; never use `last_changed` to make a repeated unchanged zero fresh.
- The HA history query must include `significant_changes_only=0`. It must omit the `no_attributes` key completely so HA returns attributes. Do not send `no_attributes=false`.
- Use `last_reported` for source transport freshness and source spread. Treat Envoy `last_updated`/value age as diagnostic data, not as a common freshness limit.
- Use an initial maximum source report age/spread of 180 seconds.
- A negative balance from `-200 W` through `0 W` becomes numeric `0 W` with `valid_noise_clamp`. A more negative balance may become `held_estimate` when the sources are report-fresh and a recent valid estimate exists.
- After the 5-minute hold expires, or when P1, Sigen, or required Envoy data is missing/stale, report numeric last-known value with `load_quality=invalid`; cron excludes it and fails closed after retry.
- Remove the `binary_sensor.emhass_mpc_fresh` dependency from `binary_sensor.emhass_envoy_required`.
- Retry load retrieval and complete validation at most twice total, with a 90-second delay. The retry must not relax freshness or quality rules.
- Test runner environment: run template tests and cron tests using the project virtualenv at `/Users/halil/workspace/misc/reterminal/.venv/bin/python` with `PYTHONPATH=scripts/lib`.
- Do not deploy, restart Home Assistant, change Docker images, or create a git commit as part of implementing this plan unless the operator separately authorizes those actions.
- Do not print or store tokens, passwords, complete authorization headers, or other secrets in diagnostics, tests, or status files.

---

## File Map

| File | Changes |
|---|---|
| `home-assistant/ha_emhass_sensors.yaml` | Add `variables:` block to `sensor.emhass_house_load_sample_w` trigger block for single-pass evaluation of sources, ages, night exclusion, balance, and quality. Implement stateful 5-minute hold with `this.attributes.last_valid_at`. Keep `sensor.emhass_house_load_power_w` as raw balance diagnostic. Add `binary_sensor.emhass_house_load_valid`. Remove `emhass_mpc_fresh` from `binary_sensor.emhass_envoy_required`. |
| `home-assistant/ha_emhass_monitoring.yaml` | Include `binary_sensor.emhass_house_load_valid` in house-load failure monitoring so an invalid estimate has a direct reason, independent of MPC freshness. |
| `home-assistant/ha_emhass_dashboard.yaml` | Show load quality, raw balance, hold age, and reason beside the separate MPC freshness status. |
| `home-assistant/dashboards/emhass_trading.yaml` | Point the "Derived house load" card and 24h history graph to `sensor.emhass_house_load_sample_w` so transient Envoy lag does not display negative house load. |
| `emhass-cron/fetch_prices_emhass.py` | Add `significant_changes_only=0` and omit `no_attributes`. Parse `sampled_at`, `load_quality`, and `hold_age_s`. Maintain `mode="healthy"` when all bins covered. Surface quality errors directly without masking as staleness. Add bounded 90-second retry helper. |
| `emhass-cron/tests/test_fetch_prices_emhass.py` | Test history request query params, unchanged zero parsing, quality validation, hold age expiry, quality-vs-stale error precedence, and retry behavior. |
| `scripts/tests/test_ha_emhass_sensors.py` | Test YAML contract for trigger `variables:`, quality attributes, numeric boot fallback, raw balance, and `emhass_house_load_valid`. |
| `scripts/tests/test_emhass_envoy_gate_templates.py` | Test Envoy gating without `emhass_mpc_fresh` and verify daylight Envoy requirement persists during stale MPC. |
| `scripts/tests/test_emhass_trading_dashboard.py` | Verify trading dashboard points to `sensor.emhass_house_load_sample_w` for derived house load. |
| `scripts/tests/test_emhass_actionable_alerts.py` | Verify invalid load quality produces actionable alert without treating `held_estimate` as failure. |
| `docs/ENTITY_CONTRACT.md` | Document `sensor.emhass_house_load_sample_w` quality contract, raw diagnostic entity, load-valid entity, and updated Envoy gate. |
| `emhass-cron/README.md` | Document history query params, source cadence, quality states, 5-minute hold, boundary risk, retry, and fail-closed behavior. |
| `docs/superpowers/specs/2026-09-02-emhass-load-quality-design.md` | Correct query example to omit `no_attributes` and clarify that `mode` remains `healthy` when all bins are covered with held estimate. |

---

## Implementation Tasks

### Task 1: Lock the Home Assistant history request contract

**Files:**
- Modify: `emhass-cron/tests/test_fetch_prices_emhass.py:712-760`
- Modify: `emhass-cron/fetch_prices_emhass.py:224-257`

**Interfaces:**
- `fetch_load_history_from_ha(secrets_path, entity_id, *, start, end, timeout=15, credentials=None) -> list[dict[str, Any]]`
- Query string MUST contain:
  - `filter_entity_id={entity_id}`
  - `end_time={end.isoformat()}`
  - `significant_changes_only=0`
- Query string MUST NOT contain `no_attributes`.

- [ ] **Step 1: Write the failing test**

In `emhass-cron/tests/test_fetch_prices_emhass.py`, update `test_uses_existing_secrets_and_home_assistant_history_endpoint`:

```python
    def test_uses_existing_secrets_and_home_assistant_history_endpoint(
        self, mock_urlopen
    ):
        record = {
            "entity_id": "sensor.emhass_house_load_sample_w",
            "state": "250",
            "last_changed": "2026-07-26T15:31:00+02:00",
            "last_updated": "2026-07-26T15:31:00+02:00",
            "attributes": {
                "sampled_at": "2026-07-26T15:31:00+02:00",
                "load_quality": "valid",
            },
        }
        mock_urlopen.return_value = self.response([[record]])

        with tempfile.TemporaryDirectory() as tmp:
            secrets = Path(tmp) / "secrets.yaml"
            secrets.write_text(
                "hass_url: http://ha.local:8123\n"
                "long_lived_token: history-token\n",
                encoding="utf-8",
            )
            start = datetime.fromisoformat("2026-07-26T14:30:00+02:00")
            end = datetime.fromisoformat("2026-07-26T15:34:00+02:00")

            records = fetch_prices_emhass.fetch_load_history_from_ha(
                secrets,
                "sensor.emhass_house_load_sample_w",
                start=start,
                end=end,
            )

        self.assertEqual(records, [record])
        request = mock_urlopen.call_args.args[0]
        parsed = urlsplit(request.full_url)
        self.assertEqual(
            unquote(parsed.path),
            "/api/history/period/2026-07-26T14:30:00+02:00",
        )
        query_params = parse_qs(parsed.query)
        self.assertEqual(
            query_params,
            {
                "filter_entity_id": ["sensor.emhass_house_load_sample_w"],
                "end_time": ["2026-07-26T15:34:00+02:00"],
                "significant_changes_only": ["0"],
            },
        )
        self.assertNotIn("no_attributes", query_params)
        self.assertEqual(
            request.get_header("Authorization"),
            "Bearer history-token",
        )
```

Also add a test verifying consecutive unchanged `0` samples are preserved:

```python
    def test_fetch_load_history_returns_consecutive_unchanged_samples(
        self, mock_urlopen
    ):
        records = [
            {
                "entity_id": "sensor.emhass_house_load_sample_w",
                "state": "0",
                "last_changed": "2026-09-01T10:20:00+00:00",
                "last_updated": f"2026-09-01T10:2{i}:00+00:00",
                "attributes": {
                    "sampled_at": f"2026-09-01T10:2{i}:00+00:00",
                    "load_quality": "valid_noise_clamp",
                },
            }
            for i in range(5)
        ]
        mock_urlopen.return_value = self.response([records])
        with tempfile.TemporaryDirectory() as tmp:
            secrets = Path(tmp) / "secrets.yaml"
            secrets.write_text("hass_url: http://ha:8123\nlong_lived_token: tok\n", encoding="utf-8")
            res = fetch_prices_emhass.fetch_load_history_from_ha(
                secrets,
                "sensor.emhass_house_load_sample_w",
                start=datetime(2026, 9, 1, 10, 20, tzinfo=timezone.utc),
                end=datetime(2026, 9, 1, 10, 25, tzinfo=timezone.utc),
            )
        self.assertEqual(len(res), 5)
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest emhass-cron/tests/test_fetch_prices_emhass.py -k test_uses_existing_secrets_and_home_assistant_history_endpoint
```
Expected: FAIL with assertion error comparing query parameters (`significant_changes_only` missing, `no_attributes` present).

- [ ] **Step 3: Update `fetch_load_history_from_ha` in `emhass-cron/fetch_prices_emhass.py`**

Replace lines 238–244 of `emhass-cron/fetch_prices_emhass.py` with:

```python
        query = urlencode(
            {
                "filter_entity_id": entity_id,
                "end_time": end.isoformat(),
                "significant_changes_only": "0",
            }
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest emhass-cron/tests/test_fetch_prices_emhass.py -k test_uses_existing_secrets_and_home_assistant_history_endpoint
```
Expected: PASS.

---

### Task 2: Add quality-aware load-history parser and validator

**Files:**
- Modify: `emhass-cron/fetch_prices_emhass.py:286-432`
- Modify: `emhass-cron/tests/test_fetch_prices_emhass.py:96-380`

**Interfaces:**
- Constants:
  ```python
  LOAD_QUALITY_VALID = "valid"
  LOAD_QUALITY_VALID_NOISE_CLAMP = "valid_noise_clamp"
  LOAD_QUALITY_HELD_ESTIMATE = "held_estimate"
  LOAD_QUALITY_INVALID = "invalid"
  USABLE_LOAD_QUALITIES = (
      LOAD_QUALITY_VALID,
      LOAD_QUALITY_VALID_NOISE_CLAMP,
      LOAD_QUALITY_HELD_ESTIMATE,
  )
  MAX_HELD_LOAD_AGE_S = 300
  ```
- Parser:
  ```python
  @dataclass
  class LoadHistorySample:
      value: float
      timestamp_utc: datetime
      quality: str
      reason: str | None = None
      hold_age_s: float | None = None
  ```
- Summary dictionary:
  `mode`: `LOAD_HISTORY_MODE_HEALTHY` | `LOAD_HISTORY_MODE_DEGRADED` | `LOAD_HISTORY_MODE_UNSAFE` (strictly for 30-min bin coverage).
  `quality`: worst usable quality across samples (`valid` -> `valid_noise_clamp` -> `held_estimate`).
  `quality_counts`: dict with counts for each quality.
  `quality_reason`: latest reason string if degraded/invalid.

- [ ] **Step 1: Write failing unit tests for quality parsing and error precedence**

In `emhass-cron/tests/test_fetch_prices_emhass.py`, add:

```python
    def test_repeated_zero_with_fresh_sampled_at_is_healthy(self):
        now = datetime(2026, 9, 1, 10, 30, tzinfo=timezone.utc)
        records = []
        for i in range(32):
            bin_time = now - timedelta(minutes=30 * (31 - i))
            records.append({
                "entity_id": "sensor.emhass_house_load_sample_w",
                "state": "0",
                "last_changed": "2026-09-01T00:00:00+00:00",
                "last_updated": bin_time.isoformat(),
                "attributes": {
                    "sampled_at": bin_time.isoformat(),
                    "load_quality": "valid_noise_clamp",
                },
            })
        summary = fetch_prices_emhass.validate_load_history_records(
            records, prediction_horizon=32, step_min=30, now=now
        )
        self.assertEqual(summary["mode"], "healthy")
        self.assertEqual(summary["covered_bins"], 32)
        self.assertEqual(summary["quality"], "valid_noise_clamp")

    def test_held_estimate_within_300s_is_usable(self):
        now = datetime(2026, 9, 1, 10, 30, tzinfo=timezone.utc)
        records = [
            {
                "entity_id": "sensor.emhass_house_load_sample_w",
                "state": "500",
                "last_changed": (now - timedelta(minutes=30 * (31 - i))).isoformat(),
                "last_updated": (now - timedelta(minutes=30 * (31 - i))).isoformat(),
                "attributes": {
                    "sampled_at": (now - timedelta(minutes=30 * (31 - i))).isoformat(),
                    "load_quality": "valid" if i < 31 else "held_estimate",
                    "hold_age_s": 120 if i == 31 else 0,
                    "estimate_reason": "envoy_update_lag" if i == 31 else None,
                },
            }
            for i in range(32)
        ]
        summary = fetch_prices_emhass.validate_load_history_records(
            records, prediction_horizon=32, step_min=30, now=now
        )
        self.assertEqual(summary["mode"], "healthy")  # all 32 bins covered!
        self.assertEqual(summary["quality"], "held_estimate")
        self.assertEqual(summary["quality_reason"], "envoy_update_lag")

    def test_invalid_newest_sample_reports_quality_error_not_stale(self):
        now = datetime(2026, 9, 1, 10, 30, tzinfo=timezone.utc)
        records = [
            {
                "entity_id": "sensor.emhass_house_load_sample_w",
                "state": "500",
                "last_changed": (now - timedelta(minutes=30 * (31 - i))).isoformat(),
                "last_updated": (now - timedelta(minutes=30 * (31 - i))).isoformat(),
                "attributes": {
                    "sampled_at": (now - timedelta(minutes=30 * (31 - i))).isoformat(),
                    "load_quality": "valid",
                },
            }
            for i in range(31)
        ]
        # At now - 1 min (fresh!), sample is marked invalid
        records.append({
            "entity_id": "sensor.emhass_house_load_sample_w",
            "state": "500",
            "last_changed": (now - timedelta(minutes=1)).isoformat(),
            "last_updated": (now - timedelta(minutes=1)).isoformat(),
            "attributes": {
                "sampled_at": (now - timedelta(minutes=1)).isoformat(),
                "load_quality": "invalid",
                "invalid_reason": "hold_expired",
            },
        })
        with self.assertRaises(fetch_prices_emhass.LoadHistoryError) as ctx:
            fetch_prices_emhass.validate_load_history_records(
                records, prediction_horizon=32, step_min=30, now=now
            )
        self.assertIn("hold_expired", str(ctx.exception))
        self.assertNotIn("is stale", str(ctx.exception))
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest emhass-cron/tests/test_fetch_prices_emhass.py -k test_repeated_zero_with_fresh_sampled_at_is_healthy
```
Expected: FAIL.

- [ ] **Step 3: Implement `_parse_load_history_record` and update `validate_load_history_records`**

In `emhass-cron/fetch_prices_emhass.py`:
1. Implement `_parse_load_history_record`:
   - Extracts numeric `state >= 0`.
   - Reads `attributes.sampled_at` first, falling back to `last_updated`.
   - Reads `load_quality` (default `LOAD_QUALITY_VALID` if omitted for backwards compatibility).
   - If `load_quality == LOAD_QUALITY_HELD_ESTIMATE`, asserts `hold_age_s is not None and 0 <= hold_age_s <= MAX_HELD_LOAD_AGE_S`. If hold age > 300s, reclassifies as `LOAD_QUALITY_INVALID`.
2. In `validate_load_history_records`:
   - Parse all records.
   - Inspect the newest record by timestamp. If the newest record has `age_min <= 5` but `quality == "invalid"`, raise `LoadHistoryError(f"Load history sample invalid: {sample.reason or 'unknown'}", summary=summary)`.
   - Filter usable samples (`quality in USABLE_LOAD_QUALITIES`).
   - Check `newest_age_min <= 5` on usable samples.
   - Compute `covered_bins_utc` across usable samples.
   - Determine `mode`: if all required bins covered -> `LOAD_HISTORY_MODE_HEALTHY`. If 1–6 interior gap -> `LOAD_HISTORY_MODE_DEGRADED`. Else -> `LOAD_HISTORY_MODE_UNSAFE`.
   - Determine `quality`: worst quality among usable samples (`valid` -> `valid_noise_clamp` -> `held_estimate`).

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest emhass-cron/tests/test_fetch_prices_emhass.py -k test_repeated_zero_with_fresh_sampled_at_is_healthy
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest emhass-cron/tests/test_fetch_prices_emhass.py -k test_held_estimate_within_300s_is_usable
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest emhass-cron/tests/test_fetch_prices_emhass.py -k test_invalid_newest_sample_reports_quality_error_not_stale
```
Expected: PASS.

---

### Task 3: Implement stateful lag-aware estimator in `ha_emhass_sensors.yaml`

**Files:**
- Modify: `scripts/tests/test_ha_emhass_sensors.py`
- Modify: `home-assistant/ha_emhass_sensors.yaml:90-154`

**Interfaces:**
- `sensor.emhass_house_load_sample_w`:
  - 1-minute trigger (`time_pattern: seconds: "0"`).
  - Single `variables:` block under trigger.
  - State: numeric `W`, round(0).
  - Attributes:
    - `sampled_at` (ISO timestamp)
    - `load_quality` (`valid`, `valid_noise_clamp`, `held_estimate`, `invalid`)
    - `raw_balance_w` (float)
    - `last_valid_at` (ISO timestamp)
    - `last_valid_w` (float)
    - `hold_age_s` (float)
    - `invalid_reason` (string or none)
    - `estimate_reason` (string or none)
    - `source_ages_s` (dict: p1, batt, envoy)
    - `source_spread_s` (float)
    - `envoy_value_age_s` (float)
- `sensor.emhass_house_load_power_w`: unclamped instantaneous raw balance `grid + pv - batt` (diagnostic).
- `binary_sensor.emhass_house_load_valid`: `on` for usable qualities, `off` for invalid.

- [ ] **Step 1: Write failing contract tests in `scripts/tests/test_ha_emhass_sensors.py`**

Update `scripts/tests/test_ha_emhass_sensors.py` to assert:
- `sensor.emhass_house_load_sample_w` has trigger `variables:`.
- `sensor.emhass_house_load_sample_w` attributes contain `sampled_at`, `load_quality`, `raw_balance_w`, `last_valid_at`, `last_valid_w`, `hold_age_s`.
- `binary_sensor.emhass_house_load_valid` exists and checks `load_quality != 'invalid'`.
- Template handles boot gracefully (missing `this.state` / `this.attributes`).

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest scripts/tests/test_ha_emhass_sensors.py
```
Expected: FAIL.

- [ ] **Step 3: Update `home-assistant/ha_emhass_sensors.yaml`**

In `home-assistant/ha_emhass_sensors.yaml`:
1. Change `sensor.emhass_house_load_power_w` to expose unclamped `raw_balance_w` without `max(..., 0)`:
   ```yaml
         - name: EMHASS house load power
           unique_id: emhass_house_load_power_w
           default_entity_id: sensor.emhass_house_load_power_w
           unit_of_measurement: W
           device_class: power
           state_class: measurement
           state: >
             {% set raw_envoy = '__ENVOY_PV_ENTITY__' %}
             {% set required = is_state('binary_sensor.emhass_envoy_required', 'on') %}
             {% set raw_e = states[raw_envoy] %}
             {% set bad = ['unknown', 'unavailable', 'none', ''] %}
             {% set envoy_bad = states(raw_envoy) in bad %}
             {% set pv = 0 if ((not required) and envoy_bad) else (states(raw_envoy) | float(0) * 1000) %}
             {% set grid = states('__P1_POWER_NET_ENTITY__') | float(0) %}
             {% set batt = states('sensor.__SIGEN_PLANT_SLUG___plant_battery_power') | float(0) %}
             {{ (grid + pv - batt) | round(0) }}
   ```
2. Rewrite `sensor.emhass_house_load_sample_w` trigger block with `variables:`:
   ```yaml
     - trigger:
         - platform: time_pattern
           seconds: "0"
       variables:
         p1_entity: '__P1_POWER_NET_ENTITY__'
         batt_entity: 'sensor.__SIGEN_PLANT_SLUG___plant_battery_power'
         envoy_entity: '__ENVOY_PV_ENTITY__'
         envoy_req: "{{ is_state('binary_sensor.emhass_envoy_required', 'on') }}"
         p1_state: "{{ states[p1_entity] }}"
         batt_state: "{{ states[batt_entity] }}"
         envoy_state: "{{ states[envoy_entity] }}"
         bad: "{{ ['unknown', 'unavailable', 'none', ''] }}"
         p1_bad: "{{ p1_state is none or p1_state.state in bad }}"
         batt_bad: "{{ batt_state is none or batt_state.state in bad }}"
         envoy_bad: "{{ envoy_state is none or envoy_state.state in bad }}"
         now_ts: "{{ as_timestamp(now()) }}"
         p1_age: "{{ (now_ts - as_timestamp(p1_state.last_reported, default=0)) if (p1_state is not none and p1_state.last_reported is defined) else 9999 }}"
         batt_age: "{{ (now_ts - as_timestamp(batt_state.last_reported, default=0)) if (batt_state is not none and batt_state.last_reported is defined) else 9999 }}"
         envoy_report_age: "{{ (now_ts - as_timestamp(envoy_state.last_reported, default=0)) if (envoy_state is not none and envoy_state.last_reported is defined) else 9999 }}"
         envoy_value_age: "{{ (now_ts - as_timestamp(envoy_state.last_updated, default=0)) if (envoy_state is not none and envoy_state.last_updated is defined) else 9999 }}"
         sources_stale: >
           {{ p1_bad or batt_bad or p1_age > 180 or batt_age > 180 or (envoy_req and (envoy_bad or envoy_report_age > 180)) }}
         source_spread: >
           {% if envoy_req %}
             {{ [p1_age, batt_age, envoy_report_age] | max - [p1_age, batt_age, envoy_report_age] | min }}
           {% else %}
             {{ [p1_age, batt_age] | max - [p1_age, batt_age] | min }}
           {% endif %}
         grid_w: "{{ states(p1_entity) | float(0) }}"
         batt_w: "{{ states(batt_entity) | float(0) }}"
         pv_w: "{{ 0 if ((not envoy_req) and envoy_bad) else (states(envoy_entity) | float(0) * 1000) }}"
         raw_bal: "{{ grid_w + pv_w - batt_w }}"
         prev_valid_ts: >
           {{ as_timestamp(this.attributes.last_valid_at, default=0) if (this is defined and this.attributes is defined and this.attributes.last_valid_at is defined) else 0 }}
         prev_valid_w: >
           {{ this.attributes.last_valid_w | float(none) if (this is defined and this.attributes is defined and this.attributes.last_valid_w is defined) else none }}
         hold_age: "{{ (now_ts - prev_valid_ts) if prev_valid_ts > 0 else 9999 }}"
         quality_and_val: >
           {% if sources_stale or source_spread > 180 %}
             {{ ('invalid', 'source_stale_or_spread', (prev_valid_w if prev_valid_w is not none else 0)) }}
           {% elif raw_bal >= 0 %}
             {{ ('valid', 'none', raw_bal | round(0)) }}
           {% elif raw_bal >= -200 %}
             {{ ('valid_noise_clamp', 'none', 0) }}
           {% elif prev_valid_w is not none and hold_age <= 300 %}
             {{ ('held_estimate', 'envoy_update_lag', prev_valid_w) }}
           {% else %}
             {{ ('invalid', 'hold_expired' if prev_valid_w is not none else 'no_prior_valid', (prev_valid_w if prev_valid_w is not none else 0)) }}
           {% endif %}
       sensor:
         - name: EMHASS house load sample
           unique_id: emhass_house_load_sample_w
           default_entity_id: sensor.emhass_house_load_sample_w
           unit_of_measurement: W
           device_class: power
           state_class: measurement
           state: "{{ quality_and_val[2] }}"
           attributes:
             sampled_at: "{{ now().isoformat() }}"
             load_quality: "{{ quality_and_val[0] }}"
             invalid_reason: "{{ quality_and_val[1] if quality_and_val[0] == 'invalid' else none }}"
             estimate_reason: "{{ quality_and_val[1] if quality_and_val[0] == 'held_estimate' else none }}"
             raw_balance_w: "{{ raw_bal | round(0) }}"
             last_valid_at: "{{ now().isoformat() if quality_and_val[0] in ['valid', 'valid_noise_clamp'] else (this.attributes.last_valid_at if (this is defined and this.attributes is defined and this.attributes.last_valid_at is defined) else none) }}"
             last_valid_w: "{{ raw_bal | round(0) if quality_and_val[0] == 'valid' else (0 if quality_and_val[0] == 'valid_noise_clamp' else prev_valid_w) }}"
             hold_age_s: "{{ hold_age if quality_and_val[0] == 'held_estimate' else 0 }}"
             source_spread_s: "{{ source_spread | round(1) }}"
             envoy_value_age_s: "{{ envoy_value_age | round(1) }}"
             grid_w: "{{ grid_w | round(0) }}"
             pv_w: "{{ pv_w | round(0) }}"
             battery_w: "{{ batt_w | round(0) }}"
   ```
3. Add `binary_sensor.emhass_house_load_valid`:
   ```yaml
         - name: EMHASS house load valid
           unique_id: emhass_house_load_valid
           default_entity_id: binary_sensor.emhass_house_load_valid
           device_class: safety
           state: >
             {{ state_attr('sensor.emhass_house_load_sample_w', 'load_quality') in ['valid', 'valid_noise_clamp', 'held_estimate'] }}
           attributes:
             quality: "{{ state_attr('sensor.emhass_house_load_sample_w', 'load_quality') }}"
             reason: "{{ state_attr('sensor.emhass_house_load_sample_w', 'invalid_reason') or state_attr('sensor.emhass_house_load_sample_w', 'estimate_reason') }}"
             sampled_at: "{{ state_attr('sensor.emhass_house_load_sample_w', 'sampled_at') }}"
             hold_age_s: "{{ state_attr('sensor.emhass_house_load_sample_w', 'hold_age_s') }}"
   ```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest scripts/tests/test_ha_emhass_sensors.py
```
Expected: PASS.

---

### Task 4: Decouple Envoy input gate from MPC output freshness

**Files:**
- Modify: `scripts/tests/test_emhass_envoy_gate_templates.py`
- Modify: `home-assistant/ha_emhass_sensors.yaml:8-56`
- Modify: `docs/ENTITY_CONTRACT.md`

**Interfaces:**
- `binary_sensor.emhass_envoy_required` state must be:
  `{{ sun_up and dawn_grace_elapsed and forecast_material }}`
- `gate_reason` must not contain `skip_mpc_stale`.

- [ ] **Step 1: Write failing template test in `scripts/tests/test_emhass_envoy_gate_templates.py`**

Add a test asserting that when sun is up, dawn grace has elapsed, and forecast >= 150 W, Envoy is required EVEN IF `binary_sensor.emhass_mpc_fresh` is `off`:

```python
    def test_envoy_required_when_sun_up_grace_elapsed_and_forecast_material_even_if_mpc_stale(self):
        # Setup context where sun_up=true, dawn_grace_elapsed=true, forecast=200, but emhass_mpc_fresh='off'
        # Evaluate template; assert result is 'True' and gate_reason is 'require_fresh'
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
PYTHONPATH=scripts/lib /Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest scripts/tests/test_emhass_envoy_gate_templates.py -k test_envoy_required_when_sun_up_grace_elapsed_and_forecast_material_even_if_mpc_stale
```
Expected: FAIL.

- [ ] **Step 3: Remove `mpc_fresh` from `binary_sensor.emhass_envoy_required` in `ha_emhass_sensors.yaml`**

In `home-assistant/ha_emhass_sensors.yaml`:
1. Remove `{% set mpc_fresh = is_state('binary_sensor.emhass_mpc_fresh', 'on') %}` from `state:` and change the return line to:
   ```jinja
   {{ sun_up and dawn_grace_elapsed and forecast_material }}
   ```
2. In `gate_reason:`, remove the `elif not mpc_fresh: skip_mpc_stale` check.

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
PYTHONPATH=scripts/lib /Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest scripts/tests/test_emhass_envoy_gate_templates.py
```
Expected: PASS (all tests pass).

---

### Task 5: Add bounded load-history retry to cron

**Files:**
- Modify: `emhass-cron/fetch_prices_emhass.py:1744-1770`
- Modify: `emhass-cron/tests/test_fetch_prices_emhass.py`

**Interfaces:**
- Function:
  ```python
  def validate_native_load_history_with_retry(
      secrets_path: Path,
      share_dir: Path,
      *,
      prediction_horizon: int,
      tz_name: str,
      now: datetime | None = None,
      load_history_config: tuple[str, int] | None = None,
      load_history_credentials: tuple[str, str] | None = None,
      max_attempts: int = 2,
      retry_delay_s: float = 90.0,
  ) -> dict[str, Any]:
  ```
- Persisted summary includes `retry: {"attempts": int, "recovered": bool, "delay_s": float}`.

- [ ] **Step 1: Write failing unit test for retry mechanism**

In `emhass-cron/tests/test_fetch_prices_emhass.py`, test:
1. First attempt fails with `LoadHistoryError`, second attempt succeeds after 90s sleep.
2. Both attempts fail -> raises `LoadHistoryError` and writes status with `retry: {"attempts": 2, "recovered": false}`.
3. Dry-run does not sleep and executes single attempt.

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest emhass-cron/tests/test_fetch_prices_emhass.py -k test_load_history_retry
```
Expected: FAIL.

- [ ] **Step 3: Implement retry helper in `emhass-cron/fetch_prices_emhass.py`**

Implement `validate_native_load_history_with_retry` and wire it into the `args.action == "mpc"` flow before `run_mpc_chain`. In dry-run, pass `max_attempts=1`.

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest emhass-cron/tests/test_fetch_prices_emhass.py -k test_load_history_retry
```
Expected: PASS.

---

### Task 6: Update monitoring, alerts, and dashboard displays

**Files:**
- Modify: `home-assistant/ha_emhass_monitoring.yaml:35-70`
- Modify: `home-assistant/ha_emhass_dashboard.yaml:20-60`
- Modify: `home-assistant/dashboards/emhass_trading.yaml:55-80`
- Modify: `scripts/tests/test_emhass_trading_dashboard.py`
- Modify: `scripts/tests/test_emhass_actionable_alerts.py`

**Interfaces:**
- `binary_sensor.emhass_fail_house_load` checks `binary_sensor.emhass_house_load_valid == 'off'`.
- `dashboards/emhass_trading.yaml` uses `sensor.emhass_house_load_sample_w` for "Derived house load" and the 24h history chart.

- [ ] **Step 1: Write failing test in `scripts/tests/test_emhass_trading_dashboard.py`**

Assert that the "Actual (Sigen / P1)" card and 24h chart reference `sensor.emhass_house_load_sample_w` rather than unclamped raw `sensor.emhass_house_load_power_w`.

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest scripts/tests/test_emhass_trading_dashboard.py
```
Expected: FAIL.

- [ ] **Step 3: Update `emhass_trading.yaml`, `ha_emhass_monitoring.yaml`, and `ha_emhass_dashboard.yaml`**

1. In `dashboards/emhass_trading.yaml`, change lines 59 and 74 from `sensor.emhass_house_load_power_w` to `sensor.emhass_house_load_sample_w`.
2. In `home-assistant/ha_emhass_monitoring.yaml`, update `binary_sensor.emhass_fail_house_load` to turn on when `is_state('binary_sensor.emhass_house_load_valid', 'off')`.
3. In `home-assistant/ha_emhass_dashboard.yaml`, add entity row displaying `binary_sensor.emhass_house_load_valid` and its attributes (`load_quality`, `reason`, `hold_age_s`).

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest scripts/tests/test_emhass_trading_dashboard.py
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest scripts/tests/test_emhass_actionable_alerts.py
```
Expected: PASS.

---

### Task 7: Update entity contract, cron documentation, and spec

**Files:**
- Modify: `docs/ENTITY_CONTRACT.md`
- Modify: `emhass-cron/README.md`
- Modify: `docs/superpowers/specs/2026-09-02-emhass-load-quality-design.md`

- [ ] **Step 1: Update documentation**
  - In `docs/ENTITY_CONTRACT.md`, document `sensor.emhass_house_load_sample_w` quality states (`valid`, `valid_noise_clamp`, `held_estimate`, `invalid`), `binary_sensor.emhass_house_load_valid`, and the raw diagnostic `sensor.emhass_house_load_power_w`.
  - In `emhass-cron/README.md`, document `significant_changes_only=0`, omitting `no_attributes`, 5-minute hold, 90-second retry, and fail-closed safety.
  - In `docs/superpowers/specs/2026-09-02-emhass-load-quality-design.md`, correct any reference from `no_attributes=false` to omitting `no_attributes`.

---

### Task 8: Incident replay test and full test suite verification

**Files:**
- Modify: `emhass-cron/tests/test_fetch_prices_emhass.py`

- [ ] **Step 1: Implement 2026-09-01 incident replay test**

In `emhass-cron/tests/test_fetch_prices_emhass.py`:
Replay the exact sequence from 2026-09-01:
1. Minute 0–20: normal load (~1300 W, `valid`).
2. Minute 20: P1=-200 W, Batt=2630 W, Envoy=1661 W -> raw balance -1169 W. Estimator holds previous valid load (`held_estimate`, hold age advances 60s, 120s, 180s, 240s, 300s).
3. Verify that at minute 25 (hold age 300s), validator passes and mode is `healthy` (all 32 bins covered).
4. Verify that at minute 26 (hold age 360s > 300s), estimator marks `invalid, reason: hold_expired`, and validator raises `LoadHistoryError("Load history sample invalid: hold_expired")`.
5. Verify retry recovery: attempt 1 fails at minute 26, attempt 2 (90s later, when Envoy updates to 2600 W) succeeds.

- [ ] **Step 2: Run all test suites**

```bash
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest emhass-cron/tests/test_fetch_prices_emhass.py
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest scripts/tests/test_ha_emhass_sensors.py
PYTHONPATH=scripts/lib /Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest scripts/tests/test_emhass_envoy_gate_templates.py
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest scripts/tests/test_emhass_trading_dashboard.py
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest scripts/tests/test_emhass_actionable_alerts.py
./scripts/render-ha-packages.sh
```
Expected: All suites PASS cleanly.

---

## Completion Criteria

- `fetch_prices_emhass.py` queries HA history with `significant_changes_only=0` and omits `no_attributes`.
- Consecutive unchanged zero samples are retained by cron and pass validation when `sampled_at` is fresh.
- `sensor.emhass_house_load_sample_w` uses a clean `variables:` block under trigger, computes quality, holds the estimate for up to 5 minutes during Envoy lag, and excludes Envoy at night.
- `load_history["mode"]` remains strictly the 30-minute bin coverage mode (`healthy`, `degraded`, `unsafe`) and never triggers false B6 alerts.
- When an invalid sample is encountered within 5 minutes, cron surfaces the `quality_reason` rather than claiming the sample is stale.
- `binary_sensor.emhass_envoy_required` no longer depends on `binary_sensor.emhass_mpc_fresh`.
- One 90-second retry is executed on load validation failure and recorded in `last_enever_run.json`.
- The trading dashboard shows the non-negative estimated load instead of unclamped negative raw power.
- All unit and contract tests pass.
