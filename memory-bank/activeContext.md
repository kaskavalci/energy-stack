# Active Context

**Last updated:** 2026-07-11

## Current focus

**MPC recovery (2026-07-11)** — MPC was failing since ~Jul 10 17:31 CEST: live `config.json` still pointed load keys at `sensor.emhass_house_load_power_w` (HA history HTTP 500). Fixed by:
1. Load history placeholder → `sensor.zonneplan_current_electricity_tariff` (stable recorder; actual load still constant 300 W via runtime list).
2. Deleted `/data/params.pkl` + `emhass` restart after sensor change.
3. Fixed `secrets_emhass.yaml` `hass_url` trailing slash (`http://192.168.0.109:8123/`) — without it `publish-data` failed posting to `8123api/states/...`.
4. Synced `emhass-cron/fetch_prices_emhass.py` with `alpha=1.0` / `beta=0.0` safe-blending constants.

Verified: `mpc.ok: true` at 2026-07-11 06:26 CEST; fresh `sensor.p_batt_forecast` and 32-slot plan in HA.

**Revert target:** When `sensor.electricity_meter_power_consumption` full-day history returns HTTP 200 for EMHASS’s “yesterday” window, switch load keys back from Zonneplan placeholder. **Re-test from 2026-07-12 00:00 UTC** (yesterday becomes Jul 11, which already returns 200). Jul 10 full-day queries still fail (HTTP 500) due to **corrupted recorder hour 14:00–15:00 UTC** on all P1 power sensors — not entity recreation (registry `created_at` May 22–23). Optional: purge/repair that hour in HA recorder to unblock sooner.

**Actionable Alerts Policy (HA)** — Deployed (2026-07-01). See `memory-bank/notebook/emhass-actionable-alerts-policy.md`.

**Load forecast** — constant 300 W per MPC slot; `sensor.emhass_house_load_power_w` for observability only (not EMHASS config).

## Next tasks

1. **SOC execution headroom** — `memory-bank/notebook/emhass-soc-min-trading.md` (proposed, not implemented)
2. **P1 history stability** — monitor `electricity_meter_power_*` history API; revert load placeholder when stable
3. **Stale SOC sensor** — `sensor.sigen_0_plant_battery_soc` stuck at 10.7% since Jul 10 23:00 (sigenergy2mqtt); investigate separately

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
- Deploy reterminal HA packages before energy-stack HA packages (P1 aliases).
