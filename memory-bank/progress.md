# Progress

## Completed

- [x] EMHASS + Zonneplan + Sigen trading stack (energy host + HA host)
- [x] Alert hygiene (B5 gate, 120 min cache age)
- [x] Constant 300 W load forecast in cron MPC
- [x] C2 night Envoy false-alert fix (HA templates)
- [x] Additive HA config merge (`scripts/lib/ha_config_merge.py`)
- [x] Repo split from reterminal monorepo (2026-06-30)
- [x] MPC recovery after load-sensor history 500 + publish URL slash fix (2026-07-11)

## Known issues

- SOC execution headroom not implemented (`memory-bank/notebook/emhass-soc-min-trading.md`)
- `emhass_control_allowed` freshness gating not implemented
- Package keys still prefixed `reterminal_emhass_*` on live HA (rename deferred)
- P1 mqtt power entities return HA history HTTP 500 for **2026-07-10 full-day** queries (bad recorder hour 14 UTC) — load placeholder uses Zonneplan tariff until **2026-07-12+** re-test
- `sensor.sigen_0_plant_battery_soc` stale since 2026-07-10 23:00 UTC

## Roadmap

- [ ] SOC apply-time clamp
- [ ] Freshness gating before EMS apply
- [ ] Optional coordinated release tags with reterminal repo
