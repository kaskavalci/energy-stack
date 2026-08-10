# Setup guide

**Last updated:** 2026-08-05

Operator guide for a fresh clone of **energy-stack**.

## Prerequisites

- Docker Compose on the **energy host**
- Home Assistant **Container** (Docker Compose) with Zonneplan HACS — see [ha-odroid-container-migration.md](ha-odroid-container-migration.md)
- **[reterminal](https://github.com/)** P1 MQTT aliases deployed first — see [ENTITY_CONTRACT.md](ENTITY_CONTRACT.md)
- Sigenergy inverter + `sigenergy2mqtt` (optional, for battery MPC + EMS)

## Quick start

```bash
./scripts/setup-local.sh
```

Creates gitignored files from examples:

| File | Purpose |
|------|---------|
| `config/site.env` | Hosts, SSH, entity IDs, Modbus, Open-Meteo location |
| `config/wud.env` | WUD Telegram notification secrets |
| `emhass/secrets_emhass.yaml` | HA URL + long-lived token |
| `emhass/share/config.json` | EMHASS plant parameters |
| `sigenergy/sigenergy2mqtt.yaml` | Modbus target |
| `mosquitto/config/mosquitto.conf` | Local MQTT broker |

Copy `config/wud.env.example` to `config/wud.env` and set its Telegram values.
`setup-local.sh` does this copy if the file does not exist. Never commit
`config/wud.env` or its values.

Edit `config/site.env` and `emhass/secrets_emhass.yaml`, then re-render:

```bash
./scripts/render-ha-packages.sh
```

## EMHASS / Open-Meteo location

Set `LAT`, `LON`, `ALT`, `TIME_ZONE`, and `TZ` in `config/site.env`. Copy the
coordinates and elevation from Home Assistant under **Settings → System →
General**. `TIME_ZONE` and `TZ` must be the same IANA timezone, for example
`Europe/Amsterdam`.

`deploy-energy-stack.sh` validates these values before uploading anything and
recreates EMHASS so cached parameters cannot retain the container image's
default location.

## Home Assistant (Container)

HA runs as Docker Container on the HA host (Odroid), not HAOS Supervisor.

| `site.env` key | Purpose | Preferred default |
|----------------|---------|-------------------|
| `HA_SSH_HOST` / `HA_SSH_PORT` | SSH deploy target | `ha` / `22` |
| `HA_CONFIG_DIR` | Host path mounted as `/config` | `/opt/homeassistant/config` |
| `HA_COMPOSE_DIR` | Dir with `docker-compose.yml` | `/opt/homeassistant` |
| `HA_COMPOSE_FILE` | Optional explicit compose file | unset |
| `HA_CONTAINER_NAME` | Container / service name | `homeassistant` |

Tracked Compose template: [`home-assistant/docker-compose.yml`](../home-assistant/docker-compose.yml). Deploy script uploads into `${HA_CONFIG_DIR}`, checks config via `docker exec`, and restarts via `docker compose` — no `ha` CLI and no interactive sudo (deploy user must own the config dir and be in the `docker` group).

Pi → Odroid cutover: [ha-odroid-container-migration.md](ha-odroid-container-migration.md).

## Home Assistant secrets

Add to `${HA_CONFIG_DIR}/secrets.yaml` on Home Assistant (not in this repo):

```yaml
emhass_publish_url: http://<ENERGY_HOST_IP>:5000/action/publish-data
```

See [ha-secrets.example.yaml](ha-secrets.example.yaml). The URL must be reachable **from the HA host** (LAN IP of the energy host).
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
# 1. Energy host (ENERGY_REMOTE_HOST)
./scripts/deploy-energy-stack.sh

# 2. Home Assistant (renders packages + preflights secrets)
./scripts/deploy-home-assistant.sh
```

`deploy-home-assistant.sh` always runs `render-ha-packages.sh` first and aborts if `emhass_publish_url` is missing from HA `secrets.yaml`. It requires a writable `${HA_CONFIG_DIR}` and passwordless `docker` / `docker compose` on the HA host.

## Docker Compose

Requires `config/site.env` (run `setup-local.sh` first). `emhass`, `emhass-cron`,
and `energy-monitor` load site variables from `config/site.env` via `env_file`.

```bash
docker compose --env-file config/wud.env up -d
```

## WUD image notifications

WUD runs on both hosts with image
`docker.io/getwud/wud:8.3.0@sha256:1cf5ecfdbd2389847427fd9685c4fd337ee8c74a0f584c52288d760d9a58811e`.
It checks for image updates and sends Telegram notifications. It does not update
containers automatically. Each host keeps WUD data in its persistent `/store`
volume. WUD excludes the local `emhass-cron` and `energy-monitor` builds.

For the energy host, run Compose with the WUD secret file:

```bash
docker compose --env-file config/wud.env up -d wud
```

The normal energy deploy does this after it validates and securely copies
`config/wud.env`. For Home Assistant, `deploy-home-assistant.sh` copies the file
to the selected Compose directory and always uses Docker Compose with its
`--env-file` option.

The unauthenticated WUD UI is available on the LAN address configured by
`ENERGY_REMOTE_HOST` at port **3000** and on the LAN address configured by
`HA_SSH_HOST` at port **3001**. Docker Compose port mappings bind all interfaces
by default. Configure host firewall rules to allow trusted LAN access only. Do
not expose these UIs to untrusted networks. Verify that access from an
untrusted network is denied. This repository does not configure firewall rules
automatically.

To deploy only WUD on the HA host, without an HA restart:

```bash
./scripts/deploy-home-assistant.sh --wud-only
```

This command makes a backup of the HA Compose file in
`${HA_CONFIG_DIR}/backups/` before it replaces the file. To roll back, use this
order on the HA host:

1. Set `HA_COMPOSE_PATH` to the selected Compose file. Set `HA_WUD_ENV_PATH` to
   `wud.env` in the same directory.
2. Stop WUD with the selected Compose project before you restore the backup:

   ```bash
   docker compose -f "${HA_COMPOSE_PATH}" --env-file "${HA_WUD_ENV_PATH}" stop wud
   ```

3. Copy the selected backup to `${HA_COMPOSE_PATH}`.
4. Check the restored Compose file with the same environment file:

   ```bash
   docker compose -f "${HA_COMPOSE_PATH}" --env-file "${HA_WUD_ENV_PATH}" config --quiet
   ```

Do not restart Home Assistant unless the rollback requires it and the operator
approves it. Keep the WUD store volume for a later retry.

## Sigenergy native fallback

`input_boolean.emhass_auto_control_master` controls whether EMHASS may use
Sigenergy command modes. Turning it off does not require an operator to select an
inverter mode manually: `script.sigen_restore_native_control` selects
`Maximum Self-consumption (Default)` automatically.

Restoration is successful only when Home Assistant confirms all of the following:

1. The Remote EMS control mode is `Maximum Self-consumption (Default)`.
2. The charging and discharging limit entities are available.
3. Each restored limit equals the smaller of its operator cap and the device's
   MQTT-advertised `max` attribute, within 0.02 kW.
4. `input_datetime.emhass_last_limit_write` advances after those confirmations.

If mode or limit confirmation times out, keep the master off and investigate the
`EMHASS D2` notification. Home Assistant start or a Sigenergy mode-entity reconnect
while the master is off reruns the same restoration transaction, including when
the inverter reports native mode immediately.

## Sigen diagnostics dashboard

Sidebar: **Sigen diagnostics** (`dashboards/sigen_diagnostics.yaml`).
Read-only plant health (temps, SOC/SOH, alarms, EMS posture) plus
`sigenergy2mqtt` health MQTT sensors (`expire_after: 180`) and Modbus/MQTT metrics.
Registered via energy-stack deploy merge (`sigen-diagnostics`).
Entity IDs: [ENTITY_CONTRACT.md](ENTITY_CONTRACT.md) § Sigen diagnostics.

## Security notes

- Do not expose EMHASS port **5000** or Mosquitto **1883** to the internet without authentication.
- Never commit `secrets_emhass.yaml`, `config/site.env`, `config/wud.env`, or live `config.json`.
