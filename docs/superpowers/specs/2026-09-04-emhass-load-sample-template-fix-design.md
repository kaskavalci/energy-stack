# EMHASS House Load Sample Template Resolution Design

**Date:** 2026-09-04  
**Status:** Approved  
**Scope:** Home Assistant sensor template (`home-assistant/ha_emhass_sensors.yaml`) and template unit testing  
**Change type:** Bug fix and reliability improvement  

---

## 1. Summary

On 2026-09-03, a stateful lag estimator was introduced in Home Assistant (`sensor.emhass_house_load_sample_w`) to prevent false history gaps when the slower Envoy PV update rate produces transient negative balances. 

However, in the deployed triggered template sensor, intermediate variables `p1_state: "{{ states[p1_entity] }}"`, `batt_state`, and `envoy_state` were defined in `variables:`. Home Assistant's template sensor engine evaluates each variable independently, stringifying non-primitive `TemplateState` objects into strings. Subsequent expressions like `p1_state.last_reported is defined` evaluated to `False`, causing `p1_age`, `batt_age`, and `envoy_report_age` to default to `9999`.

This made `sources_stale` permanently `True`, setting `load_quality: invalid` with reason `source_stale_or_spread` on every sample. In turn, `emhass-cron`'s strict validation check failed closed and aborted every MPC run today.

This design eliminates the intermediate state objects in favor of direct property access on `states[entity_id]`, restores valid sample generation, and adds automated Home Assistant template evaluation testing.

---

## 2. Template Changes (`ha_emhass_sensors.yaml`)

In `home-assistant/ha_emhass_sensors.yaml`, under `sensor.emhass_house_load_sample_w`:

1. **Remove intermediate state objects**:
   - Delete `p1_state: "{{ states[p1_entity] }}"`
   - Delete `batt_state: "{{ states[batt_entity] }}"`
   - Delete `envoy_state: "{{ states[envoy_entity] }}"`

2. **Access entity state and timestamps directly**:
   ```yaml
   p1_bad: "{{ states[p1_entity] is none or states[p1_entity].state in bad }}"
   batt_bad: "{{ states[batt_entity] is none or states[batt_entity].state in bad }}"
   envoy_bad: "{{ states[envoy_entity] is none or states[envoy_entity].state in bad }}"

   p1_age: "{{ (now_ts - as_timestamp(states[p1_entity].last_reported, default=0)) if (states[p1_entity] is not none and states[p1_entity].last_reported is defined) else 9999 }}"
   batt_age: "{{ (now_ts - as_timestamp(states[batt_entity].last_reported, default=0)) if (states[batt_entity] is not none and states[batt_entity].last_reported is defined) else 9999 }}"
   envoy_report_age: "{{ (now_ts - as_timestamp(states[envoy_entity].last_reported, default=0)) if (states[envoy_entity] is not none and states[envoy_entity].last_reported is defined) else 9999 }}"
   envoy_value_age: "{{ (now_ts - as_timestamp(states[envoy_entity].last_updated, default=0)) if (states[envoy_entity] is not none and states[envoy_entity].last_updated is defined) else 9999 }}"
   ```

3. **Retain downstream logic and attribute contract**:
   - `sources_stale`, `source_spread`, `grid_w`, `batt_w`, `pv_w`, `raw_bal`, `prev_valid_ts`, `prev_valid_w`, `hold_age`, and `quality_and_val` remain unchanged.
   - Sensor state (`{{ quality_and_val[2] }}`) and all attributes (`sampled_at`, `load_quality`, `invalid_reason`, `estimate_reason`, `raw_balance_w`, `last_valid_at`, `last_valid_w`, `hold_age_s`, `source_spread_s`, `envoy_value_age_s`, `grid_w`, `pv_w`, `battery_w`) remain unchanged.

---

## 3. Testing Strategy

1. **Static Contract Test (`scripts/tests/test_ha_emhass_sensors.py`)**:
   - Update assertions in `test_sample_sensor_has_trigger_variables_block()` to verify `states[p1_entity].last_reported`, `states[batt_entity].last_reported`, and `states[envoy_entity].last_reported` are used in `p1_age`, `batt_age`, and `envoy_report_age`.
   - Verify `p1_state`, `batt_state`, and `envoy_state` are absent from `variables:`.

2. **Dynamic Template Evaluation Test (`scripts/tests/test_ha_emhass_load_sample_template.py`)**:
   - Use `eval_ha_template` from `scripts/lib/ha_template_eval.py` to evaluate the rendered Jinja template via Home Assistant's `/api/template` endpoint.
   - Assert `sources_stale` is `False`.
   - Assert `p1_age` and `batt_age` are small positive numbers (< 60s).
   - Assert `load_quality` is not `invalid` when sources are fresh.

3. **Regression Test Suites**:
   - Run `python3 -m unittest emhass-cron/tests/test_fetch_prices_emhass.py`.
   - Run `python3 -m unittest scripts/tests/test_emhass_trading_dashboard.py`.
   - Run `python3 -m unittest scripts/tests/test_emhass_actionable_alerts.py`.
   - Run `python3 -m unittest scripts/tests/test_emhass_envoy_gate_templates.py`.

---

## 4. Verification and Deployment

1. Render HA packages locally: `./scripts/render-ha-packages.sh`.
2. Deploy to Home Assistant host: `./scripts/deploy-home-assistant.sh`.
3. Verify live sensor in HA:
   - Query `sensor.emhass_house_load_sample_w` via REST API.
   - Confirm `load_quality: valid` or `valid_noise_clamp`.
   - Confirm `invalid_reason` is `null`.
   - Confirm `last_valid_at` and `last_valid_w` are populated.
4. Run manual MPC:
   - Execute `docker exec emhass-cron python3 /app/fetch_prices_emhass.py --action mpc -v` on the energy host.
   - Confirm native load validation passes and MPC completes successfully.
