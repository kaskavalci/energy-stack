# EMHASS Native Load Forecast and Fail-Closed MPC Design

**Status:** Approved on 2026-07-26

## Objective

Remove the temporary 300 W house-load fill and eliminate the intermittent
`cannot convert float NaN to integer` MPC failures without introducing a
second forecasting subsystem.

The solution uses a regularly sampled Home Assistant load sensor as EMHASS's
native load-history input. If that input or Home Assistant is unavailable, the
MPC run fails closed. The existing plan-freshness control then returns
Sigenergy to safe native operation.

## Evidence

### NaN failure

EMHASS 0.17.5 still retrieves and mixes Home Assistant history during
`naive-mpc-optim`. The configured load-history placeholder is the hourly
Zonneplan tariff sensor. When dense PV history extends the joined dataframe to
a `:30` timestamp, the tariff column has no value at that timestamp.

EMHASS then evaluates:

```python
first_fcst = alpha * df_forecast.iloc[0] + beta * df_now[col].iloc[-1]
```

`beta=0.0` does not protect against a missing current value because
`0.0 * NaN` remains `NaN`. The subsequent integer conversion fails.

### The 300 W assumption is obsolete

The constant was introduced while the house was unoccupied and HA recorder
history was unreliable. The house has been occupied since 2026-07-19.

For the six complete occupied days from 2026-07-20 through 2026-07-25:

- daily half-hour mean load was approximately 507–659 W;
- daily half-hour median load was approximately 432–510 W;
- a 300 W walk-forward forecast had 272 W MAE;
- it underpredicted by 259 W on average.

The constant is no longer a defensible baseline.

## Decision

Use the simplest native path:

1. Home Assistant records `sensor.emhass_house_load_sample_w` once per minute.
2. EMHASS uses that sensor for `sensor_power_load_no_var_loads` and
   `var_model`.
3. `load_forecast_method` remains `naive`.
4. The cron request does not send `load_power_forecast`, `alpha`, or `beta`.
5. There is no fixed, dynamic, or cached load fallback.

The native naive method uses the most recent `prediction_horizon` resampled
history values and maps them to the forecast horizon. With the configured
32-step, 30-minute horizon, the sensor needs at least 16 hours of history
before cutover.

## Home Assistant sampled sensor

The existing `sensor.emhass_house_load_power_w` remains the source of truth for
instantaneous calculated house load. It changes very frequently and has
produced roughly 75,000–97,000 recorder rows per day.

Add a trigger-based sensor with:

- entity ID: `sensor.emhass_house_load_sample_w`;
- cadence: once per minute at second `0`;
- unit: W;
- device class: power;
- state class: measurement;
- state: the numeric value of `sensor.emhass_house_load_power_w`;
- availability: false when the source is non-numeric;
- `sampled_at` attribute: current ISO timestamp, forcing a recorder update even
  when the numeric load is unchanged.

This gives EMHASS regular timestamps, explicit watts, bounded history volume,
and predictable 30-minute resampling. The faster HA hardware makes the extra
sensor acceptable, but the bounded cadence remains useful for data quality.

## EMHASS configuration

The source-of-truth example and rendered live configuration must contain:

```json
{
  "historic_days_to_retrieve": 1,
  "optimization_time_step": 30,
  "prediction_horizon": 32,
  "sensor_power_load_no_var_loads": "sensor.emhass_house_load_sample_w",
  "var_model": "sensor.emhass_house_load_sample_w",
  "load_forecast_method": "naive",
  "sensor_power_photovoltaics": "sensor.emhass_pv_power_w",
  "sensor_power_photovoltaics_forecast": "sensor.emhass_pv_power_w",
  "sensor_linear_interp": [
    "sensor.emhass_house_load_sample_w",
    "sensor.emhass_pv_power_w"
  ],
  "sensor_replace_zero": [
    "sensor.emhass_pv_power_w"
  ]
}
```

The sampled load is interpolated but is not placed in `sensor_replace_zero`.
A real zero load is valid. PV retains its existing zero-replacement behavior.

After changing EMHASS sensor keys, restart the EMHASS container or remove its
cached `/data/params.pkl` so it cannot retain the old entity names.

## Cron simplification

Delete the temporary load-list implementation from
`emhass-cron/fetch_prices_emhass.py`:

- `LOAD_FORECAST_CONSTANT_W`;
- `LOAD_FORECAST_MODE`;
- `LOAD_FORECAST_MIX_ALPHA`;
- `LOAD_FORECAST_MIX_BETA`;
- `fetch_current_load`;
- `build_load_power_forecast`;
- the `--load-sensor` CLI option;
- the live load fetch in `main`;
- load-fallback metadata in dry-run and status output.

Keep `build_naive_mpc_payload`, but reduce it to the inputs EMHASS still needs:

```python
{
    "weather_forecast_cache_only": True,
    "prediction_horizon": prediction_horizon,
    # optional SOC fields
}
```

EMHASS selects the configured native `naive` load method because no runtime
list overrides it.

## Failure policy

There is deliberately no load fallback.

If HA history is unavailable, incomplete, or invalid:

1. `naive-mpc-optim` fails;
2. cron writes `mpc.ok=false`;
3. cron does not call `finalize_successful_mpc_run`;
4. `input_datetime.emhass_last_successful_mpc` is not advanced;
5. `binary_sensor.emhass_mpc_fresh` becomes false when the last success reaches
   40 minutes;
6. the existing stale-plan automation disables Remote EMS and restores
   Sigenergy native control.

The five-minute `publish-data` automation does not advance the successful-MPC
timestamp, so republishing an old plan cannot bypass this gate.

This system-level fail-safe replaces all fabricated load values.

## Deployment

Deployment is intentionally split:

1. Deploy only the HA sampled sensor and its documentation/tests.
2. Verify it is numeric, in watts, and recording once per minute.
3. Wait until it has at least 16 hours of usable history.
4. Verify the HA history endpoint returns the sampled data.
5. Deploy the cron simplification and EMHASS configuration.
6. Restart EMHASS to clear cached parameters.
7. Run a manual MPC and verify a natural `:30` scheduled MPC.
8. Observe at least 24 hours, including multiple `:00` and `:30` runs.

Do not keep the 300 W path for the waiting period. The old configuration stays
active until the sampled sensor has enough history, then the cutover happens
once.

## Rollback

If the native load cutover fails:

1. disable Remote EMS through the existing master control;
2. restore the prior EMHASS configuration backup;
3. restart EMHASS to reload cached parameters;
4. leave the sampled HA sensor in place for diagnosis.

Do not restore the 300 W forecast as an automatic fallback. A rollback is an
explicit operator action while Sigenergy remains in native control.

## Out of scope

- a custom rolling time-of-day profile;
- a dynamic scalar baseline;
- a cached load forecast;
- weekday/weekend or machine-learning forecasting;
- patching the installed EMHASS package;
- changing the 40-minute plan-freshness policy;
- changing price, weather, PV, or SOC policy beyond the entity wiring above.

These can be considered later only if measured native-naive forecast quality
is insufficient. Reliability is addressed first with fewer moving parts.
