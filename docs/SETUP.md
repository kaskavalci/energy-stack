# Setup guide

**Last updated:** 2026-06-30

Operator guide for a fresh clone of **energy-stack**.

## Prerequisites

- Docker Compose on the **energy host**
- Home Assistant with Zonneplan HACS integration
- **[reterminal](https://github.com/)** P1 MQTT aliases deployed first — see [ENTITY_CONTRACT.md](ENTITY_CONTRACT.md)
- Sigenergy inverter + `sigenergy2mqtt` (optional, for battery MPC + EMS)

## Quick start

```bash
./scripts/setup-local.sh
```

Creates gitignored files from examples:

| File | Purpose |
|------|---------|
| `config/site.env` | Hosts, SSH, entity IDs, Modbus |
| `emhass/secrets_emhass.yaml` | HA URL + long-lived token |
| `emhass/share/config.json` | EMHASS plant parameters |
| `sigenergy/sigenergy2mqtt.yaml` | Modbus target |
| `mosquitto/config/mosquitto.conf` | Local MQTT broker |

Edit `config/site.env` and `emhass/secrets_emhass.yaml`, then re-render:

```bash
./scripts/render-ha-packages.sh
```

## Home Assistant secrets

Add to `/config/secrets.yaml` on Home Assistant (not in this repo):

```yaml
emhass_publish_url: http://<ENERGY_HOST_IP>:5000/action/publish-data
```

See [ha-secrets.example.yaml](ha-secrets.example.yaml). The URL must be reachable **from the HA host** (LAN IP of the energy Pi).

## Entity placeholders

HA package sources use `__TOKEN__` strings rendered at deploy time from `config/site.env`:

| `site.env` key | Rendered into |
|----------------|---------------|
| `ENVOY_PV_ENTITY` | `__ENVOY_PV_ENTITY__` |
| `SIGEN_PLANT_SLUG` | `__SIGEN_PLANT_SLUG__` inside `sensor/select/number.*_plant_*` |
| `P1_POWER_NET_ENTITY` | `__P1_POWER_NET_ENTITY__` |
| `P1_POWER_CONSUMPTION_ENTITY` | `__P1_POWER_CONSUMPTION_ENTITY__` |
| `P1_POWER_PRODUCTION_ENTITY` | `__P1_POWER_PRODUCTION_ENTITY__` |

**Sigenergy slug pattern:** `sensor.__SIGEN_PLANT_SLUG___plant_battery_soc` renders to `sensor.sigen_0_plant_battery_soc` when `SIGEN_PLANT_SLUG=sigen_0` (triple underscore before `plant` is intentional).

Find your Envoy entity under **Settings → Devices → Enphase Envoy**. Sigenergy entities come from `sigenergy2mqtt` discovery (`sigen_0` for the first plant).

## Deploy order

```bash
# 1. Energy host (.135 or ENERGY_REMOTE_HOST)
./scripts/deploy-energy-stack.sh

# 2. Home Assistant (renders packages + preflights secrets)
./scripts/deploy-home-assistant.sh
```

`deploy-home-assistant.sh` always runs `render-ha-packages.sh` first and aborts if `emhass_publish_url` is missing from HA `secrets.yaml`.

## Docker Compose

Requires `config/site.env` (run `setup-local.sh` first). `emhass-cron` and `energy-monitor` load sensor overrides from `site.env` via `env_file`.

```bash
docker compose up -d
```

## Security notes

- Do not expose EMHASS port **5000** or Mosquitto **1883** to the internet without authentication.
- Never commit `secrets_emhass.yaml`, `config/site.env`, or live `config.json`.
