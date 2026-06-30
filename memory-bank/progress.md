# Progress

## Completed

- [x] EMHASS + Zonneplan + Sigen trading stack (energy host + HA host)
- [x] Alert hygiene (B5 gate, 120 min cache age)
- [x] Constant 300 W load forecast in cron MPC
- [x] C2 night Envoy false-alert fix (HA templates)
- [x] Additive HA config merge (`scripts/lib/ha_config_merge.py`)
- [x] Repo split from reterminal monorepo (2026-06-30)

## Known issues

- SOC execution headroom not implemented (`memory-bank/notebook/emhass-soc-min-trading.md`)
- `emhass_control_allowed` freshness gating not implemented
- Package keys still prefixed `reterminal_emhass_*` on live HA (rename deferred)

## Roadmap

- [ ] SOC apply-time clamp
- [ ] Freshness gating before EMS apply
- [ ] Optional coordinated release tags with reterminal repo
