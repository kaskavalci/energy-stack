# HA Odroid Container migration

**Last updated:** 2026-07-21  
**Author:** Composer

Migrate Home Assistant from HAOS on the Pi 3 (`.109`) to Docker Container on Armbian Odroid N2 (`192.168.0.75`, SSH alias `ha`). Energy stack stays on `.135`.

## Goals

- More RAM for HA (~4 GiB Odroid vs ~900 MiB Pi) — addresses confirmed HA OOM.
- Deploy without Supervisor: `${HA_CONFIG_DIR}`, `docker exec` config check, `docker compose` restart.
- Keep Odroid IP **`.75`** (do not reclaim `.109`).

## Architecture after cutover

| Piece | Choice |
|-------|--------|
| Runtime | `ghcr.io/home-assistant/home-assistant:stable` via Compose |
| Network | `network_mode: host` |
| Preferred config dir | `/opt/homeassistant/config` |
| Interim layout (no sudo) | `/home/halil/homeassistant/config` |
| SSH | `ssh ha` (port 22) |
| IP | `192.168.0.75` |

Tracked Compose: [`home-assistant/docker-compose.yml`](../home-assistant/docker-compose.yml). Volume is `./config:/config` relative to the compose directory.

## Pitfall — Container `TZ` vs HA `time_zone` (2026-07-21)

**Symptom:** MPC publishes OK (`mpc.ok=true`) but `binary_sensor.emhass_mpc_fresh` is off with `reason=plan_stale`, `age_min` ≈ **−120** (negative), and `emhass_control_allowed=off` while master is on.

**Cause:** HA Container image defaults to **UTC** if compose omits `TZ`. HA `time_zone` is `Europe/Amsterdam`. Templates that did `as_timestamp(states('input_datetime…'))` treated naive wall-clock strings as UTC → age ≈ −(CEST offset) → false `plan_stale`.

**Fix (must stay in git — next deploy must not regress):**

1. Compose: `environment.TZ: Europe/Amsterdam` in [`home-assistant/docker-compose.yml`](../home-assistant/docker-compose.yml) (recreate container after change; `docker exec … date` must show CEST, not UTC).
2. Templates: freshness + apply cooldown use `state_attr(..., 'timestamp')` (epoch), not `as_timestamp(states(...))` — see `ha_emhass_dashboard.yaml` / `ha_sigen_ems.yaml`.

Do not remove `TZ` from compose even if templates use the attribute; both stay.

## Dual energy managers (hard rule)

While Pi HA (`.109`) and Odroid HA (`.75`) can both be up, **only one may run Sigenergy/EMHASS control**.

1. On Pi: set `input_boolean.emhass_auto_control_master` **off**.
2. Take the full backup **after** master is off (restore boots with master off).
3. Restore on Odroid; leave master **off**.
4. Shut down Pi HAOS (or keep as read-only rollback with master still off).
5. Point `hass_url` / `site.env` at `.75`, verify sensors/MPC dry paths.
6. Only then turn master **on** on Odroid.

**Do not** enable master on Odroid while Pi HA can still apply EMS commands.

## Cutover checklist

### A — Odroid bringup (before touching Pi)

- [x] Docker Engine + Compose plugin installed; deploy user in `docker` group.
- [x] Layout exists and is writable without sudo:
  - Preferred: `sudo mkdir -p /opt/homeassistant/config && sudo chown -R "$USER:$USER" /opt/homeassistant` (still optional — `/opt` needs password sudo)
  - Interim (in use): `/home/halil/homeassistant/{config,docker-compose.yml}`
- [x] Copy repo `home-assistant/docker-compose.yml` to that directory.
- [x] `docker compose pull` (image ready).
- [x] Set `HA_CONFIG_DIR` / `HA_COMPOSE_DIR` / `HA_CONTAINER_NAME` in `config/site.env` to match (cutover-verify 2026-07-21; interim home path).
- [x] Do **not** leave a long-lived empty HA racing Pi with master on (restored config; master off).

### B — Backup on Pi (HAOS)

- [x] Master **off** on Pi (REST confirmed 2026-07-21).
- [x] Full backup via `ha backups new --name pre-odroid-migrate-20260721` (slug `a9e2fdc6`, ~2.1 GiB; landed on default NAS backup mount).
- [x] Tar on Odroid: `/home/halil/homeassistant/backups-import/pre-odroid-migrate-20260721.tar`.
- [x] Pi kept bootable for rollback; master remains off.

### C — Restore on Odroid

- [x] Offline extract of `homeassistant.tar.gz` → `/home/halil/homeassistant/config/` (excluded regenerable `deps/` + nested `backups/`); then `docker compose up -d`.
- [x] API on `http://192.168.0.75:8123` confirms master **off** (same long-lived token as Pi).
- [x] Confirm UI, entities, HACS (Zonneplan), MQTT (P1, Sigenergy), Envoy load — **master still off**. Post-sanitization: `default_config`/`backup`/`cloud` OK; EMHASS publish sensors await cutover of `hass_url`.
- [x] Confirm config `secrets.yaml` has `emhass_publish_url: http://192.168.0.135:5000/action/publish-data`.
- [x] Deploy user can write interim `${HA_CONFIG_DIR}` and run `docker` / `docker compose` without sudo.

### D — Cutover wiring

- [x] Keep Odroid on `192.168.0.75`.
- [x] Update `config/site.env`: `HA_SSH_HOST=ha`, `HA_SSH_PORT=22`, interim `HA_CONFIG_DIR`/`HA_COMPOSE_DIR`/`HA_CONTAINER_NAME` (2026-07-21).
- [x] Update energy-host `emhass/secrets_emhass.yaml`: `hass_url: http://192.168.0.75:8123/`; restarted emhass + cron + monitor.
- [x] Long-lived token: existing token works on `.75` API.
- [x] Smoke with master still off: Zonneplan/P1/Sigen/Envoy/MQTT OK; MPC dry-run + live publish created `p_*_forecast` on Odroid (`mpc.ok=true`).
- [x] Stop Pi HA Core (`ha core stop`, not host shutdown) — 2026-07-21 ~22:44 CEST; Pi OS still up for rollback.
- [x] Master **on** on Odroid + control verify (~23:10 CEST) after TZ + timestamp-attr fix; see pitfall above.

### E — Decommission Pi HA

- [ ] After stable window: full Pi HAOS host shutdown or wipe (Core already stopped).
- [ ] Drop HAOS-only paths from operator notes / memory-bank.

## Rollback

1. Master **off** on both until one side is chosen.
2. Revert `site.env` + `hass_url` to Pi `.109`.
3. Power Pi HA; leave Odroid HA stopped (`docker compose down` in `$HA_COMPOSE_DIR`).

## Deploy knobs (`config/site.env`)

| Knob | Purpose | Default / preferred |
|------|---------|---------------------|
| `HA_CONFIG_DIR` | HA `/config` on host | `/opt/homeassistant/config` |
| `HA_COMPOSE_DIR` | Directory with `docker-compose.yml` | `/opt/homeassistant` |
| `HA_COMPOSE_FILE` | Explicit compose file (overrides dir) | unset |
| `HA_CONTAINER_NAME` | Container / compose service name | `homeassistant` |

`./scripts/deploy-home-assistant.sh` uploads into `${HA_CONFIG_DIR}`, runs `docker exec … hass --script check_config`, then `docker compose … restart`. No `ha` CLI, no interactive sudo.

## Explicit non-goals

- Not moving EMHASS/MQTT onto Odroid.
- Not HA Supervised / HAOS on Odroid.
- Not host firewall rules.
- Not reclaiming `.109` for HA.
- Not fixing Pi recorder corruption during migrate.

## Bringup status (2026-07-21)

Done via SSH (`ssh ha` → Odroid `.75` as `halil`):

- Docker 29.x + Compose v5.x present; user in `docker` group.
- `/opt` not writable without password sudo → interim layout at `/home/halil/homeassistant/`.
- Compose copied; `ghcr.io/home-assistant/home-assistant:stable` pulled (~3.4 GB).
- HA container **not** started yet (avoids empty HA racing Pi with master on).

For interim deploy until `/opt` is owned:

```bash
# in config/site.env
HA_CONFIG_DIR=/home/halil/homeassistant/config
HA_COMPOSE_DIR=/home/halil/homeassistant
HA_CONTAINER_NAME=homeassistant
HA_SSH_HOST=ha
HA_SSH_PORT=22
```

**Backup-restore (2026-07-21):** done — tar at `/home/halil/homeassistant/backups-import/pre-odroid-migrate-20260721.tar`; config populated; HA Container up on `.75` with master **off**. Both Pi and Odroid have master off.

**Post-restore Container sanitization (2026-07-21):** Core restore was complete. The UI “Invalid config” for `hassio`/`backup`/`cloud`/`default_config` was the expected HAOS→Container Supervisor cascade (`Missing SUPERVISOR`), not a bad extract. Fixed by removing `.storage/hassio` + `homeassistant.json`, pointing `.storage/backup` agents at `backup.local` (no add-ons), and disabling `rpi_power`. After restart: `default_config`/`backup`/`cloud` load; `hassio` does not; master remains **off**. EMHASS forecast sensors appear on Odroid only after cutover of `hass_url`.

**Cutover-verify (2026-07-21):** wiring + data verification complete. Energy stack talks to `.75`.

**Master on + control (2026-07-21 ~23:10 CEST):** Pi Core stopped; master on Odroid. First enable hit false `plan_stale` (no compose `TZ`); fixed with `TZ=Europe/Amsterdam` + `state_attr(..., 'timestamp')` freshness. Control applying (Remote EMS + matching charge/discharge limits). Remaining: optional full Pi host shutdown when rollback window closes.

Operator still optional:

- One-time: `sudo mkdir -p /opt/homeassistant && sudo chown -R halil:halil /opt/homeassistant` then move compose+config and set preferred `HA_*` paths.
