# Active Context

**Last updated:** 2026-07-11 (afternoon CEST)  
**Author:** Auto

## Current focus

**MPC recovered (2026-07-11 ~13:28 CEST)** after PV history placeholder fix on energy host.

| Window (CEST) | Failure mode | Evidence |
|---|---|---|
| 07:30 – 09:30 | `ValueError: cannot convert float NaN to integer` in `get_mix_forecast` | emhass traceback; weather-cache gap |
| 10:00 – 13:23 | HA history HTTP **500** on `sensor.emhass_pv_power_w` | bad recorder hour **07:00 UTC** Jul 11 |
| 13:23 | Zonneplan placeholder for PV **rejected** by EMHASS | `sensor.power_photovoltaics and sensor.power_load_no_var_loads should not be the same` |
| 13:28 | **Recovery** with `sensor.p_pv_forecast` PV placeholder | `last_enever_run.json` → `mpc.ok: true` |

**Live config state (energy host):**
- Load keys → `sensor.zonneplan_current_electricity_tariff`
- PV keys → `sensor.p_pv_forecast` (`sensor_power_photovoltaics`, `sensor_linear_interp`, `sensor_replace_zero`)
- `hass_url` trailing slash retained

**P1 / recorder:** Jul 11 full-day history still HTTP 500 for P1 and `sensor.emhass_pv_power_w` (bad hour 07 UTC). Jul 10 still bad at hour 14 UTC. Zonneplan tariff and `sensor.p_pv_forecast` Jul 11 full-day → HTTP 200.

**Proper fix:** repair HA recorder Jul 11 07 UTC + Jul 10 14 UTC. Revert placeholders only after full-day history HTTP 200 for affected sensors.

## Repo work queue (before deploy)

**P0 (this session):** `config.json.example` placeholders, alpha/beta doc reconciliation, `fetch_current_load` unit tests — landed in repo commit pending.

**P1 at deploy:** `emhass_recovery_complete` guard, SOC floor migration notes (10%→5%), `block_reason` vs policy alignment, `render-ha-packages.sh` + merge test.

Full schedule: `memory-bank/progress.md` § "What needs to be done, when".

## Other open items

- **Stale SOC sensor** — `sensor.sigen_0_plant_battery_soc` stuck at 10.7% since Jul 10 23:00 UTC (sigenergy2mqtt); investigate separately from MPC
- **SOC execution headroom** — `memory-bank/notebook/emhass-soc-min-trading.md` (proposed, not implemented)

**Actionable Alerts Policy (HA)** — Deployed (2026-07-01). See `memory-bank/notebook/emhass-actionable-alerts-policy.md`.

**Load forecast** — constant 300 W per MPC slot; `sensor.emhass_house_load_power_w` for observability only (not EMHASS config).

## Verification

```bash
./scripts/setup-local.sh
./scripts/render-ha-packages.sh
./scripts/deploy-energy-stack.sh
./scripts/deploy-home-assistant.sh
# On energy host after config/sensor changes:
docker compose exec -T emhass rm -f /data/params.pkl && docker compose restart emhass
docker compose exec -T emhass-cron python3 /app/fetch_prices_emhass.py --action mpc -v
```

## Assumptions

- Live `config.json` on energy host may differ from git — verify via `ENERGY_REMOTE_HOST` / `ENERGY_REMOTE_DIR` in `config/site.env`.
- EMHASS requires load and PV history sensor keys to differ; Zonneplan tariff cannot serve both roles.
- Deploy reterminal HA packages before energy-stack HA packages (P1 aliases).
- Placeholder revert only after HA recorder repair and full-day history HTTP 200 for affected sensors.
