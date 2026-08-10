# Add WUD to Energy Host and HA Node Implementation Plan

> For implementation, use the superpowers:subagent-driven-development skill or the superpowers:executing-plans skill.

## Goal

Deploy What's Up Docker (WUD) 8.3.0 on the energy host and Home Assistant node. WUD watches image updates, sends grouped Telegram notifications, persists state, and never updates containers automatically. Use separate WUD secrets, preserve the normal HA deploy, and add a WUD-only HA deploy that does not restart Home Assistant.

## Architecture

Run one WUD service per host with the same pinned image, read-only Docker socket, named /store volume, LAN-only HTTP port, and Telegram trigger in batch mode. Use untracked config/wud.env. Do not put WUD secrets in config/site.env because existing energy services receive that file. Pass config/wud.env to Compose with --env-file for interpolation.

Energy host: root Compose project, port 3000, volume wud-energy-store. HA node: home-assistant/docker-compose.yml, port 3001, volume wud-ha-store. Keep Home Assistant on host networking; WUD uses bridge networking. Add wud.watch=false to locally built emhass-cron and energy-monitor. Do not configure a WUD Docker trigger or automatic update action.

## Tech Stack

- Docker Compose YAML and Bash
- Python 3 standard library, zoneinfo, unittest, and existing ruamel.yaml tests
- WUD 8.3.0 with digest sha256:1cf5ecfdbd2389847427fd9685c4fd337ee8c74a0f584c52288d760d9a58811e
- Telegram HTML trigger

## Global Constraints

- Follow AGENTS.md and use ASD-STE100 Simplified Technical English in operator text.
- Read memory-bank/activeContext.md, memory-bank/techContext.md, and .agents/skills/emhass-energy-stack/SKILL.md before implementation or live deployment.
- Read config/site.env before SSH, deploy checks, or live debugging. Use role variables; never hardcode hosts, users, ports, or device IDs in tracked files.
- Keep credentials only in config/wud.env. Never print or commit the token or chat ID.
- Keep WUD notify-only. Keep the unauthenticated UI on the trusted LAN.
- Preserve the EMHASS force-recreate sequence and normal HA deploy behavior.
- Add --wud-only to validate and deploy only WUD resources, skip the HA config check, and skip the HA restart.
- Preserve unrelated dirty-worktree changes. Do not commit unless explicitly requested.
- Use apply_patch for edits and run verification before claiming success.

---

## Phase 1: Map and write failing tests

### Task 1: Map current contracts

Inspect docker-compose.yml, home-assistant/docker-compose.yml, scripts/deploy-energy-stack.sh, scripts/deploy-home-assistant.sh, scripts/setup-local.sh, .gitignore, config/site.env.example, memory-bank/activeContext.md, and memory-bank/techContext.md.

Record that energy deploy validates the site, syncs, builds emhass-cron and energy-monitor, force-recreates emhass, and starts auxiliary services. Record that HA_COMPOSE_FILE overrides the HA Compose path, and normal HA deploy renders packages, merges configuration, checks configuration, and restarts Home Assistant. Confirm neither Compose file has WUD resources. Do not edit in this task.

### Task 2: Add tests before implementation

Create scripts/tests/test_wud_env.py. Test valid input, missing bot token, missing chat ID, invalid TZ, duplicate keys, malformed keys, blank secrets, and errors that do not expose secret values.

Create scripts/tests/test_wud_compose.py. Parse both files and assert exact image tag/digest, energy port 3000:3000, HA port 3001:3000, read-only socket, named /store volume, cron watcher, WATCHALL=true, HTML/batch/all/ONCE=true Telegram settings, role-specific batch title with an escaped count placeholder, no WUD env_file, no Docker trigger or automatic update, wud.watch=false on local builds, and unchanged homeassistant network_mode: host.

Create scripts/tests/test_wud_deploy.py. Inspect the energy script and assert validation before SSH/rsync, rsync exclusion, stdin plus umask 077 plus mode 600 plus atomic secret placement, --env-file on every remote Compose call, retained EMHASS force-recreate, and WUD in the final start.

Create scripts/tests/test_ha_wud_deploy.py. Assert --wud-only and unknown-option handling, HA_COMPOSE_FILE support, selected Compose-parent env path, secure upload, WUD-only config/pull/start with the selected env file, exit before HA check/restart, and retained normal check/restart.

Run these tests. They must fail before implementation.

## Phase 2: Implement Compose and secret handling

### Task 3: Add WUD env validation and setup

Files: .gitignore, config/wud.env.example, scripts/setup-local.sh, scripts/lib/validate_wud_env.py.

Ignore config/wud.env. Add:

    WUD_TELEGRAM_BOT_TOKEN=
    WUD_TELEGRAM_CHAT_ID=
    TZ=Europe/Amsterdam

Update setup-local.sh to copy the example without overwriting an existing file.

Implement load_wud_env(path: Path), validate_wud_env(values: Mapping[str, str]), and a CLI. Return 0 for valid input, 1 for missing or invalid values, and 2 for missing files or parse errors. Parse only KEY=VALUE; skip blanks/comments; reject duplicate keys, invalid shell-style uppercase names, and lines without equals. Strip whitespace. Validate TZ with zoneinfo.ZoneInfo. Reject empty or whitespace-containing Telegram values. Errors may name fields but must never include secret values.

Run:

    python3 -m unittest scripts.tests.test_wud_env -v

### Task 4: Add energy WUD service

File: docker-compose.yml. Add this service and top-level volume:

    wud:
      image: docker.io/getwud/wud:8.3.0@sha256:1cf5ecfdbd2389847427fd9685c4fd337ee8c74a0f584c52288d760d9a58811e
      container_name: wud
      restart: unless-stopped
      ports:
        - "3000:3000"
      volumes:
        - /var/run/docker.sock:/var/run/docker.sock:ro
        - wud-energy-store:/store
      environment:
        TZ: "${TZ:?config/wud.env must define TZ}"
        WUD_WATCHER_LOCAL_SOCKET: /var/run/docker.sock
        WUD_WATCHER_LOCAL_CRON: "0 6 * * *"
        WUD_WATCHER_LOCAL_WATCHALL: "true"
        WUD_TRIGGER_TELEGRAM_ENERGY_BOTTOKEN: "${WUD_TELEGRAM_BOT_TOKEN:?config/wud.env must define WUD_TELEGRAM_BOT_TOKEN}"
        WUD_TRIGGER_TELEGRAM_ENERGY_CHATID: "${WUD_TELEGRAM_CHAT_ID:?config/wud.env must define WUD_TELEGRAM_CHAT_ID}"
        WUD_TRIGGER_TELEGRAM_ENERGY_MESSAGEFORMAT: HTML
        WUD_TRIGGER_TELEGRAM_ENERGY_MODE: batch
        WUD_TRIGGER_TELEGRAM_ENERGY_THRESHOLD: all
        WUD_TRIGGER_TELEGRAM_ENERGY_ONCE: "true"
        WUD_TRIGGER_TELEGRAM_ENERGY_BATCHTITLE: "energy-host: $${count} update(s)"

    volumes:
      wud-energy-store:

Add wud.watch=false to emhass-cron and energy-monitor. Do not add env_file to WUD.

### Task 5: Add HA WUD service

File: home-assistant/docker-compose.yml. Add the same settings with port 3001:3000, volume wud-ha-store:/store, and:

    WUD_TRIGGER_TELEGRAM_HA_BOTTOKEN: "${WUD_TELEGRAM_BOT_TOKEN:?config/wud.env must define WUD_TELEGRAM_BOT_TOKEN}"
    WUD_TRIGGER_TELEGRAM_HA_CHATID: "${WUD_TELEGRAM_CHAT_ID:?config/wud.env must define WUD_TELEGRAM_CHAT_ID}"
    WUD_TRIGGER_TELEGRAM_HA_MESSAGEFORMAT: HTML
    WUD_TRIGGER_TELEGRAM_HA_MODE: batch
    WUD_TRIGGER_TELEGRAM_HA_THRESHOLD: all
    WUD_TRIGGER_TELEGRAM_HA_ONCE: "true"
    WUD_TRIGGER_TELEGRAM_HA_BATCHTITLE: "ha-node: $${count} update(s)"

Add top-level volume wud-ha-store. Keep homeassistant network_mode: host. Do not add WUD host networking.

Run:

    python3 -m unittest scripts.tests.test_wud_compose -v
    docker compose --env-file config/wud.env config --quiet
    docker compose -f home-assistant/docker-compose.yml --env-file config/wud.env config --quiet

## Phase 3: Implement deployment paths

### Task 6: Update energy deployment

File: scripts/deploy-energy-stack.sh.

Validate config/wud.env before SSH or rsync. Exclude it from rsync. Upload it separately through stdin to a remote temporary file, using umask 077, mode 600, and an atomic move to config/wud.env. Keep the token out of command arguments and logs.

Use --env-file config/wud.env for every remote Compose call:

    docker compose --env-file config/wud.env config --quiet
    docker compose --env-file config/wud.env pull wud
    docker compose --env-file config/wud.env build emhass-cron energy-monitor
    docker compose --env-file config/wud.env up -d --force-recreate emhass
    docker compose --env-file config/wud.env up -d emhass-cron energy-monitor wud

Keep EMHASS force-recreate and pull only WUD from the registry. Run test_wud_deploy.py and bash -n scripts/deploy-energy-stack.sh.

### Task 7: Add WUD-only HA deployment

File: scripts/deploy-home-assistant.sh.

Parse --wud-only and reject unknown options with exit code 2. Compute:

    HA_COMPOSE_PATH="${HA_COMPOSE_FILE:-${HA_COMPOSE_DIR}/docker-compose.yml}"
    HA_COMPOSE_PARENT="$(dirname "${HA_COMPOSE_PATH}")"

Keep HA_COMPOSE_ARGS=(-f "${HA_COMPOSE_PATH}") so HA_COMPOSE_FILE remains supported. Validate config/wud.env before SSH or rsync. In WUD-only mode skip package rendering, energy secret preflight, package merge, HA config check, and HA restart.

The WUD-only path must verify the selected parent is writable, back up the selected live Compose file using the existing HA backup convention, upload home-assistant/docker-compose.yml to the selected path, upload config/wud.env to the selected parent as wud.env through stdin with mode 600, run selected Compose config --quiet, pull wud, and up -d wud with the selected env file, then exit before any HA restart. Support paths with spaces and never print wud.env.

Normal mode must retain package render, additive merge, HA config check, and restart, using the selected Compose path and selected Compose-parent env file. Run test_ha_wud_deploy.py and bash -n scripts/deploy-home-assistant.sh.

## Phase 4: Document and verify

### Task 8: Update operating documentation

Files: README.md, docs/SETUP.md, AGENTS.md, memory-bank/techContext.md.

Document WUD installation on both hosts; copying config/wud.env.example to config/wud.env; the never-commit rule; UI ports 3000 and 3001 using configured role values; unauthenticated LAN-only access; notify-only behavior; persistent /store; exclusion of local builds; WUD-only HA deployment without an HA restart; and rollback with HA Compose backup.

Add the exact digest, secret-file rule, Compose --env-file rule, and no-auto-update rule to project invariants. Do not add live secrets or hardcoded host details. Update memory-bank/activeContext.md only after an approved live deployment with actual results.

### Task 9: Run local verification

Run:

    python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v
    bash -n scripts/setup-local.sh scripts/deploy-energy-stack.sh scripts/deploy-home-assistant.sh
    docker compose --env-file config/wud.env config --quiet
    docker compose -f home-assistant/docker-compose.yml --env-file config/wud.env config --quiet
    git diff --check
    git ls-files --error-unmatch config/wud.env

The final git ls-files command must fail because config/wud.env is untracked. Treat that failure as the expected secret-safety result. Confirm the exact digest is present, no WUD Docker trigger exists, and no tracked file contains a secret, host, username, or device serial. Confirm unrelated dirty-worktree files are unchanged.

### Task 10: Deploy only after explicit approval

Before live work, read config/site.env, memory-bank/activeContext.md, memory-bank/techContext.md, and .agents/skills/emhass-energy-stack/SKILL.md. Validate config/wud.env and confirm approval for both hosts.

    ./scripts/deploy-energy-stack.sh
    ./scripts/deploy-home-assistant.sh --wud-only

Verify without printing secrets: pinned digest, health endpoints, watcher and Telegram trigger through the WUD API, LAN-only UI, persistent /store after WUD restart, unchanged energy services, no HA restart in WUD-only mode, and Telegram titles identifying energy-host or ha-node. Update activeContext only after all checks pass. On failure, stop rollout and use rollback.

## Rollback

For energy failure, stop WUD and restore the previous root Compose file from version control or backup. Keep the WUD volume unless removal is explicitly requested. Remove remote config/wud.env only through deliberate host-scoped cleanup.

For HA failure, stop WUD with the selected Compose file, restore the selected Compose backup, and rerun the Compose config check. Do not restart Home Assistant unless required and approved. Keep the WUD store volume for a later retry.

After rollback, verify EMHASS, emhass-cron, energy-monitor, and Home Assistant have their pre-change state. Report what was restored and whether WUD state was retained.

## Self-review checklist

- Every task names concrete files and symbols.
- Tests are written before implementation.
- Compose interpolation uses --env-file; env_file is not used for secret interpolation.
- WUD secrets are isolated from existing application containers.
- Image, ports, socket mode, store volumes, trigger mode, and no-auto-update behavior are testable.
- HA_COMPOSE_FILE and WUD-only no-restart are covered.
- No unresolved TODO, TBD, or placeholder work remains.
- Live deployment is gated by approval and uses config/site.env role variables.
- No unrelated dirty-worktree changes are included.

