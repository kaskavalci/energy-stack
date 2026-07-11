# EMHASS Safe Blending Design

## Purpose
Prevent EMHASS from crashing with `ValueError: cannot convert float NaN to integer` at night when the Home Assistant API is slow or the load sensor is unavailable due to high load or OOM events on the RPI3.

## Constraints & Considerations
- Retain the blending behavior where the first 30-min slot uses the actual current load, and the remaining slots use 300W.
- Keep the baseline load at 300W.
- Reduce load on the HA device by removing the secondary HA query that EMHASS performs internally during optimization.
- Confine changes to `emhass-cron/fetch_prices_emhass.py` (no upstream EMHASS image changes).

## Architecture & Data Flow
Instead of passing `alpha` and `beta` to EMHASS (which forces EMHASS to perform an internal HTTP request back to HA), the cron script will handle the blending logic prior to triggering the optimization.

1. **Fetch**: `fetch_prices_emhass.py` will query the load sensor (`sensor.electricity_meter_power_consumption`) from HA via the existing `fetch_ha_state` function.
2. **Fallback Logic**: 
   - If the request succeeds and returns a valid number, we use the returned value.
   - If the request times out, returns `unavailable`/`unknown`, or raises an error, we log a warning and seamlessly fallback to `300.0`.
3. **Array Construction**:
   - The script creates the `load_power_forecast` array of length `prediction_horizon`.
   - `load_power_forecast[0] = fetched_load_or_fallback`
   - `load_power_forecast[1:] = 300.0`
4. **Payload Modification**:
   - The cron script removes `"alpha"` and `"beta"` from the `naive-mpc-optim` payload.
   - EMHASS now receives a pre-blended forecast and skips its internal HA query step entirely.

## Components to Modify

### `emhass-cron/fetch_prices_emhass.py`
- Add a new helper `fetch_current_load(secrets_path, sensor, timeout=15) -> float` that queries HA and falls back to `300.0` on failure.
- Modify `build_load_power_forecast(prediction_horizon: int, first_slot_load: float)` to set `array[0] = first_slot_load` and the rest to `300.0`.
- Update `build_naive_mpc_payload` to remove `"alpha"` and `"beta"`, and pass the customized forecast array.
- Update `run_mpc_chain` to call `fetch_current_load` before building the payload, passing it down to `build_naive_mpc_payload`.

## Error Handling
- The `fetch_current_load` function will trap `HTTPError`, `URLError`, `ValueError`, and `TypeError`. Upon failure, it logs a non-fatal warning (e.g., `Could not read current load from HA (...): HTTP Error 502`) and returns `300.0`. This ensures that a struggling HA instance does not abort the entire MPC run.
