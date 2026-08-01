---
name: emhass-energy-stack
description: >-
  Operate and debug EMHASS + Zonneplan HA prices + Sigenergy energy stack.
  Use for stale prices, MPC plans, Open-Meteo/PV, emhass-cron, energy-monitor
  alerts, or deploying this repo. Hosts and entity IDs: config/site.env.
---

# EMHASS energy stack

Home battery arbitrage: **Zonneplan HACS on HA** → CSV on the **energy host** → **EMHASS MPC** (Open-Meteo + pvlib) → **publish-data** → HA sensors → optional **Sigenergy Remote EMS**.

| Host | Role |
|------|------|
| **Energy host** (`ENERGY_REMOTE_HOST`, `ENERGY_REMOTE_DIR` in `config/site.env`) | Docker: `emhass`, `emhass-cron`, `energy-monitor`, `sigenergy2mqtt` |
| **Home Assistant** (`HA_SSH_HOST`, `HA_SSH_USER`, `HA_SSH_PORT`) | Zonneplan HACS + EMHASS packages |

**Before operating:** read `config/site.env` (gitignored) for live hosts and entity IDs.

**Config:** see [docs/SETUP.md](../../docs/SETUP.md).

**Cross-repo:** P1 entity IDs come from the **reterminal** repo's `ha_p1_mqtt_aliases.yaml`. See [docs/ENTITY_CONTRACT.md](../../docs/ENTITY_CONTRACT.md).

**Deploy:** `./scripts/deploy-energy-stack.sh` → rsync laptop → `ENERGY_REMOTE_HOST`, rebuild `emhass-cron` + `energy-monitor`.

**Docs:** [docs/SETUP.md](../../docs/SETUP.md), [docs/ENTITY_CONTRACT.md](../../docs/ENTITY_CONTRACT.md), [emhass-cron/README.md](../../emhass-cron/README.md). Operator scratch: `memory-bank/notebook/` (gitignored).

---

## Data flow

```text
Zonneplan API → HACS on HA (HA_SSH_HOST) → HA REST (each MPC) → CSV on /share → naive-mpc-optim
Open-Meteo → pickle in emhass /data/ → P_PV
publish-data → HA REST sensors
```

- HA does not read price CSVs.
- Price fetch fails → MPC aborted (no stale-CSV fallback).

---

## Cron (`emhass-cron` `/app/crontab`)

| Schedule | Command |
|----------|---------|
| **\*/30** | `--action mpc` (HA prices + optim + publish) |
| **05:05** | `--action refresh-weather` (skip if cache fresh) |

Verify after deploy (source `config/site.env` first):

```bash
source scripts/lib/source-site-env.sh
ssh "${ENERGY_REMOTE_HOST}" "docker compose -f ${ENERGY_REMOTE_DIR}/docker-compose.yml exec -T emhass-cron cat /app/crontab"
```

---

## Required `config.json` knobs

| Key | Value |
|-----|-------|
| `prediction_horizon` | **32** max; effective = min(32, Zonneplan slots), min 12 |
| `optimization_time_step` | **30** |
| `load_cost_forecast_method` / `production_price_forecast_method` | **csv** |
| `weather_forecast_method` | **open-meteo** |
| `open_meteo_cache_max_age` | **150** min — must stay **>** `optimization_time_step` (30); hard B5/fail at this age |

---

## Pitfalls / invariants

1. **`open_meteo_cache_max_age` > MPC cadence** — if equal to 30 min, JSON age trips at every `:30` boundary and MPC fails. Soft proactive refresh clears cache at `max_age − 2×step` (90 min with defaults); hard stale/B5 still uses `open_meteo_cache_max_age` (150).
2. **Force refetch = delete cache files** — EMHASS `weather-forecast-cache` is a no-op while cache files exist. Cron uses `clear_weather_cache_files()` before retry.
3. **Soft clear trades availability for freshness** — it deletes JSON + pkl before refetch, so an Open-Meteo blip at soft age can abort MPC even below the hard limit.
4. **Weather failure → one push** — monitor **B4** only; **B2_mpc** / **B2_weather** suppressed when B4 fires.
5. **Shared stale rules** — `lib/weather_cache_stale.py` used by cron and monitor.
6. **B5_cache / B5_zero_fill** — sustained-failure gate (`ALERT_FAILURE_THRESHOLD=2`).
7. **Price fetch fails → MPC aborted** — no stale-CSV fallback.

---

## Manual commands

```bash
docker compose exec emhass-cron python3 /app/fetch_prices_emhass.py --action mpc -v
docker compose exec emhass-cron python3 /app/fetch_prices_emhass.py --action mpc --dry-run -v
```

---

## Monitor alerts (energy-monitor)

| ID | Condition |
|----|-----------|
| **A4** | `prices_fetched_at.txt` > 26 h |
| **B2_prices** / **B2_mpc** | Last run `ok: false` |
| **B3** | MPC success > 40 min ago |
| **B4** | Last MPC failed (weather/PV) |
| **B5_cache** / **B5_zero_fill** | Open-Meteo cache geometry (2 consecutive failures) |

---

## Plant-specific

Entity IDs and Modbus target: `config/site.env` — see [docs/ENTITY_CONTRACT.md](../../docs/ENTITY_CONTRACT.md).

| Key | Purpose |
|-----|---------|
| `EMHASS_SOC_SENSOR` | MPC `soc_init` |
| `ZONNEPLAN_PRICE_SENSOR` | Price fetch for CSV |
| `ENVOY_PV_ENTITY` | PV templates / monitoring |
| `SIGEN_PLANT_SLUG` | Sigen `*_plant_*` entities |
| `SIGENERGY_MODBUS_HOST` | Inverter Modbus (rendered into `sigenergy2mqtt.yaml`) |

Secrets: `EMAHASS_SECRETS=/secrets/secrets_emhass.yaml` in compose; values in gitignored `emhass/secrets_emhass.yaml`.
