# Active Context

**Last updated:** 2026-06-30

## Current focus

**OSS config parameterization** — `config/site.env` + rendered HA packages + example files for public publish (2026-06-30). HA `emhass_publish_url` added to live `secrets.yaml` on HA host (2026-06-30).

**C2 night false-alert fix (HA)** — deployed 2026-06-30. Pending: night + dawn observation.

**Alert hygiene** — B5_cache sustained-failure gate; `open_meteo_cache_max_age` 120 min. See `memory-bank/notebook/emhass-alert-hygiene-2026-06.md`.

**Load forecast** — constant 300 W per MPC slot; `sensor.emhass_house_load_power_w` for observability.

## Next tasks

1. **SOC execution headroom** — `memory-bank/notebook/emhass-soc-min-trading.md` (proposed, not implemented)
2. **`binary_sensor.emhass_control_allowed`** gating — see `home-assistant/` packages and `memory-bank/notebook/`

## Verification

```bash
./scripts/setup-local.sh
./scripts/render-ha-packages.sh
./scripts/deploy-energy-stack.sh
./scripts/deploy-home-assistant.sh
```

## Assumptions

- Live `config.json` on energy host may differ from git — verify via `ENERGY_REMOTE_HOST` / `ENERGY_REMOTE_DIR` in `config/site.env`.
- Deploy reterminal HA packages before energy-stack HA packages (P1 aliases).
