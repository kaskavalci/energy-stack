# EMHASS Location and Native PV Panel Design

**Date:** 2026-07-17

## Goal

Correct the location used by EMHASS/Open-Meteo and make the Home Assistant PV
panel show recent actual production together with the remainder of today's
forecast, without adding a custom frontend dependency.

## Constraints

- Leave `input_boolean.emhass_auto_control_master` enabled. Scheduled control
  and the existing recovery automation remain responsible for resuming battery
  commands after EMHASS restarts.
- Keep the existing EMHASS cadence, horizon, CSV price methods, Open-Meteo
  method, and weather-cache age invariant unchanged.
- Home Assistant is memory-constrained. Do not install ApexCharts Card or any
  other custom Lovelace resource.
- `config/site.env` remains gitignored and stores site-specific values. Tracked
  files must not contain the site's exact coordinates.

## Location configuration

Add the EMHASS-supported variables `LAT`, `LON`, `ALT`, and `TIME_ZONE` to
`config/site.env`, populated from Home Assistant's current site configuration.
Add documented placeholders to `config/site.env.example`.

Load `config/site.env` into the `emhass` service through Docker Compose. Remove
the hard-coded EMHASS timezone so the site file is the single source for both
coordinates and timezone.

The energy-stack deploy preflight must reject missing, placeholder, non-numeric,
or out-of-range coordinates and a missing timezone. This prevents EMHASS from
silently falling back to its image defaults again.

The first deployment recreates the EMHASS container so its process environment
and `/data/params.pkl` are rebuilt. Then the existing forced weather-refresh MPC
path clears both Open-Meteo cache files before fetching a forecast.

## Native PV panel

Replace the raw 24-hour PV history graph with one native `vertical-stack`
dashboard item:

1. A two-day `statistics-graph` of hourly mean actual PV from
   `sensor.emhass_pv_power_w`. The sensor already has `state_class: measurement`,
   so Home Assistant can use compact statistics instead of loading all raw state
   changes.
2. A Markdown table containing only today's EMHASS PV forecast rows from
   `sensor.p_pv_forecast.attributes.forecasts`. It shows time and forecast watts
   and updates when the published forecast attribute changes.

This is one dashboard layout item and introduces no JavaScript resource or
backend process. Native Home Assistant cards cannot place future-dated forecast
attributes on the same graph as historical states, so the graph and forecast
table are vertically grouped rather than overlaid.

## Validation

- Structural tests prove Docker Compose loads `config/site.env` for EMHASS and
  does not retain a hard-coded timezone.
- Deploy-preflight tests cover valid, missing, placeholder, non-numeric, and
  out-of-range location values.
- Dashboard tests prove the PV item uses only native cards, hourly two-day
  statistics, and a forecast table filtered to today.
- Rendered YAML is parsed before deployment.
- Live verification confirms the EMHASS process environment, `params.pkl`, and
  Open-Meteo cache all use the configured Dutch site location and Amsterdam
  timezone, the forced MPC run succeeds, and the HA dashboard loads without a
  custom-card error.
