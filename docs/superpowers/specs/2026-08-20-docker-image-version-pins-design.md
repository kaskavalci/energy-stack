# Docker image version pins (no floating latest)

**Date:** 2026-08-20

**Author:** Composer

**Status:** Approved

**Scope:** Pin all floating `:latest` images on the energy host to fixed
release versions in `docker-compose.yml`; upgrade via WUD notify + manual
version bump after changelog review

## Problem

Energy-host Compose uses floating `:latest` for `emhass`, `sigenergy2mqtt`,
`mosquitto`, and `mqtt4dsmr`. A pull or recreate can change running bits without
an intentional version edit. WUD notifies on digest drift, but the UI copy
control often fails on plain HTTP, and `:latest` updates do not give a clear
version string to paste into Compose.

Repo policy already prefers fixed versions. EMHASS upgrades also require a
load-gap contract probe before deploy.

## Goals

- Replace every energy-host `:latest` with a **fixed release version** in
  `docker-compose.yml` (version string only; **no digest** in Compose).
- Keep WUD as notify-only (no auto-update).
- Define a repeatable upgrade loop: WUD alert → changelog → verify (EMHASS) →
  bump version → deploy.
- Update memory-bank / docs so floating-latest override language is removed.

## Non-goals

- Pinning Home Assistant (`:stable` on the HA host) in this work.
- Changing the already-pinned WUD image (`8.3.0@sha256:…`) unless a separate
  upgrade is approved.
- Adding digest pins to Compose (operator chose version-only).
- Automating Compose edits or deploys from WUD.
- Fixing the WUD UI clipboard button (browser HTTP clipboard limit).

## Decision

**Pin form:** `registry/image:<version>` only.

| Service | Image pattern |
|---------|----------------|
| `emhass` | `ghcr.io/davidusb-geek/emhass:<version>` |
| `sigenergy2mqtt` | `ghcr.io/seud0nym/sigenergy2mqtt:<version>` |
| `mosquitto` | `eclipse-mosquitto:<version>` |
| `mqtt4dsmr` | `ghcr.io/antonijn/mqtt4dsmr:<version>` |

Remove “floating `:latest` is intentional” comments from Compose.

**Why version-only:** Operator preference for a readable pin and a simple WUD
loop. Digest retag risk is accepted; EMHASS still uses the contract probe before
a version bump is deployed.

**Repo policy alignment:** Today `docs/EMHASS_UPGRADES.md`,
`scripts/verify-emhass-load-gap-contract.sh` (when reading Compose), and
`scripts/tests/test_emhass_image_pin.py` expect `tag@sha256:digest` in
Compose. This design **changes that** for Compose to version-only. Verification
still passes an explicit `version@sha256:digest` **argument** to the probe
script. Update the upgrade doc and pin test in the same change set so CI matches
operator policy.

## Architecture / workflow

### Phase 1 — Lock current stack

1. Resolve the **running** version for each of the four services on
   `ENERGY_REMOTE_HOST` (OCI labels / image metadata / registry tags that match
   the running digest). Prefer a real release tag over the word `latest`.
2. Set those versions in root `docker-compose.yml`.
3. Deploy (or recreate) only if needed so Compose matches what runs.
4. Confirm WUD still watches the four services; local builds stay
   `wud.watch: false`.

### Phase 2 — Upgrade candidates

1. Use WUD Telegram / UI / API (`http://<energy-host>:3000/api/containers`) to
   see newer versions or digests. If the UI copy button fails, use the API or
   `docker inspect` — do not block on clipboard.
2. Read upstream changelog / release notes for break risk.
3. **EMHASS only:** resolve candidate version and its registry digest → run
   `./scripts/verify-emhass-load-gap-contract.sh '<version>@sha256:<digest>'`
   (argument form; Compose stays version-only) → run updated pin/cron tests →
   record evidence under `docs/verification/emhass/` → set Compose to
   `…/emhass:<version>` only.
4. Other three services: changelog review + targeted smoke (MQTT broker, P1
   bridge, Sigenergy Modbus/MQTT) after deploy.
5. Bump version strings in Compose only when review is clean.
6. Deploy with operator approval (`./scripts/deploy-energy-stack.sh` or targeted
   recreate). Post-check: health, next MPC / bridge freshness.

### WUD role

| WUD does | WUD does not |
|----------|----------------|
| Daily watch; Telegram batch notify | Edit Compose |
| Show update available | Pull / recreate for you |
| Expose `/api/containers` for tag/digest | Auto-update containers |

## Docs / memory-bank / tests

In the same change set as Phase 1 (or immediately with the first EMHASS pin):

- `docs/EMHASS_UPGRADES.md` — Compose pin = version only; probe still uses
  version@digest as CLI argument.
- `scripts/tests/test_emhass_image_pin.py` — assert fixed non-`latest` version;
  do **not** require `@sha256:` in Compose.
- `AGENTS.md` / SETUP if they still mandate digest-in-Compose.
- `memory-bank/techContext.md` — floating `:latest` → version pins.
- `memory-bank/activeContext.md` — short note on pin + upgrade process.

## Risks

| Risk | Mitigation |
|------|------------|
| Upstream retags the same version string | Accept for this design; EMHASS probe before bump; prefer not to `pull` without a version edit |
| EMHASS minor/patch breaks load-gap / naive load | Mandatory contract probe + verification note before Compose bump |
| Sigenergy / mqtt4dsmr behavior change | Changelog gate; smoke Modbus + P1 after recreate |
| Mosquitto major jump | Prefer patch/minor; read Eclipse Mosquitto notes |
| WUD still reports digest-only while tag is `latest` | Phase 1 removes `latest`; later alerts should show version tags when available |

## Success criteria

- No `:latest` on the four energy-host services in `docker-compose.yml`.
- Each pin is a concrete release version string.
- Upgrade path documented: WUD → changelog → (EMHASS verify) → bump → deploy.
- Floating-latest language removed from tech/active context.

## Open resolution at implement time

Exact version strings for Phase 1 come from the live energy host at execute
time (not hard-coded in this spec). Phase 2 candidate versions come from WUD /
registry after Phase 1.
