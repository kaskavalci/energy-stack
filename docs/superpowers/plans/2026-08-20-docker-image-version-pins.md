# Docker Image Version Pins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace floating `:latest` on energy-host `emhass`, `sigenergy2mqtt`, `mosquitto`, and `mqtt4dsmr` with fixed release versions (no digest in Compose), align tests/docs, then review changelogs and bump/deploy approved upgrades.

**Architecture:** Phase 1 locks Compose to the versions already running on the energy host (from OCI labels). Pin tests and upgrade docs accept version-only Compose pins; the load-gap probe still requires an explicit `version@sha256:digest` CLI argument. Phase 2 resolves newer release tags from upstream, reviews changelogs, verifies EMHASS, then bumps versions and deploys with operator approval. WUD stays notify-only.

**Tech Stack:** Docker Compose, unittest + ruamel.yaml, bash verify wrapper, SSH deploy via `config/site.env`, WUD API for awareness.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-20-docker-image-version-pins-design.md`
- Compose pin form: `registry/image:<version>` only — **no** `@sha256:` in Compose
- Never use `:latest` for the four energy-host services
- WUD remains notify-only (no Docker update trigger)
- EMHASS upgrade still requires `./scripts/verify-emhass-load-gap-contract.sh '<version>@sha256:<digest>'` **before** Compose bump + deploy
- Do not change HA `:stable` or the WUD image pin unless separately approved
- Create commits only when the user explicitly authorizes; otherwise leave diffs unstaged and report suggested messages
- Before SSH/deploy: read `memory-bank/activeContext.md`, `memory-bank/techContext.md`, `.agents/skills/emhass-energy-stack/SKILL.md`; source `config/site.env` (never hardcode hosts)
- Do not enable EMHASS master or stop `sigenergy2mqtt` without operator approval

## File Map

| File | Responsibility |
|------|----------------|
| `docker-compose.yml` | Version-only image pins for four services; remove floating-latest comments |
| `scripts/tests/test_emhass_image_pin.py` | Assert EMHASS version-only pin; wrapper still rejects digest-less default |
| `scripts/tests/test_compose_image_pins.py` | Assert all four services pinned, none `:latest`, none digest |
| `scripts/verify-emhass-load-gap-contract.sh` | Clear error if Compose default lacks `@sha256:`; CLI arg still required for probe |
| `docs/EMHASS_UPGRADES.md` | Version-only Compose; probe uses CLI `version@digest` |
| `AGENTS.md` | Drop digest-in-Compose requirement for EMHASS |
| `memory-bank/techContext.md` | Version pins, not floating latest |
| `memory-bank/activeContext.md` | Pin + upgrade note after deploy |
| `docs/verification/emhass/` | Evidence note when Phase 2 bumps EMHASS |

## Live baseline (Phase 1 targets, measured 2026-08-20)

| Service | OCI `org.opencontainers.image.version` | Phase 1 Compose pin |
|---------|----------------------------------------|---------------------|
| `emhass` | `v0.17.9` | `ghcr.io/davidusb-geek/emhass:v0.17.9` |
| `sigenergy2mqtt` | `2026.7.4` | `ghcr.io/seud0nym/sigenergy2mqtt:2026.7.4` |
| `mosquitto` | `2.1.2` | `eclipse-mosquitto:2.1.2` |
| `mqtt4dsmr` | `v0.9.7` | `ghcr.io/antonijn/mqtt4dsmr:v0.9.7` |

Re-check labels on the energy host before editing if the host may have changed.

---

### Task 1: Pin tests for version-only Compose

**Files:**
- Modify: `scripts/tests/test_emhass_image_pin.py`
- Create: `scripts/tests/test_compose_image_pins.py`

**Interfaces:**
- Consumes: `docker-compose.yml` services `emhass`, `sigenergy2mqtt`, `mosquitto`, `mqtt4dsmr`
- Produces: unittest modules that fail while Compose still uses `:latest` / digest pins

- [ ] **Step 1: Rewrite EMHASS pin test for version-only**

Replace `scripts/tests/test_emhass_image_pin.py` with:

```python
from __future__ import annotations

import re
import unittest
from pathlib import Path

from ruamel.yaml import YAML


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"
WRAPPER = REPO_ROOT / "scripts" / "verify-emhass-load-gap-contract.sh"
PINNED_IMAGE = "ghcr.io/davidusb-geek/emhass:v0.17.9"
PIN_PATTERN = re.compile(
    r"^ghcr\.io/davidusb-geek/emhass:(?!latest$)v?[0-9][^@\s]+$"
)
CANDIDATE_PATTERN = re.compile(
    r"^ghcr\.io/davidusb-geek/emhass:(?!latest(?:@|$))[^@\s]+"
    r"@sha256:[0-9a-f]{64}$"
)


class EmhassImagePinTests(unittest.TestCase):
    def test_compose_uses_fixed_release_version_only(self) -> None:
        with COMPOSE.open(encoding="utf-8") as stream:
            compose = YAML(typ="safe").load(stream)
        image = compose["services"]["emhass"]["image"]
        self.assertEqual(image, PINNED_IMAGE)
        self.assertRegex(image, PIN_PATTERN)
        self.assertNotIn("@sha256:", image)
        self.assertNotIn(":latest", image)

    def test_contract_wrapper_requires_digest_candidate_arg(self) -> None:
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("docker-compose.yml", text)
        self.assertIn(
            'compose["services"]["emhass"]["image"]',
            text,
        )
        self.assertIn("emhass_load_gap_contract.py", text)
        self.assertIn("@sha256:", text)
        self.assertNotIn("config/site.env", text)
        self.assertNotIn("secrets_emhass", text)
        # Wrapper must not treat version-only Compose as a pullable candidate.
        self.assertRegex(
            text,
            r"must use repository:release@sha256:digest|Candidate must use",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Add compose pins test for all four services**

Create `scripts/tests/test_compose_image_pins.py`:

```python
from __future__ import annotations

import unittest
from pathlib import Path

from ruamel.yaml import YAML


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"

EXPECTED = {
    "emhass": "ghcr.io/davidusb-geek/emhass:v0.17.9",
    "sigenergy2mqtt": "ghcr.io/seud0nym/sigenergy2mqtt:2026.7.4",
    "mosquitto": "eclipse-mosquitto:2.1.2",
    "mqtt4dsmr": "ghcr.io/antonijn/mqtt4dsmr:v0.9.7",
}


class ComposeImagePinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with COMPOSE.open(encoding="utf-8") as stream:
            cls.compose = YAML(typ="safe").load(stream)

    def test_four_services_use_fixed_versions(self) -> None:
        for service, expected in EXPECTED.items():
            with self.subTest(service=service):
                image = self.compose["services"][service]["image"]
                self.assertEqual(image, expected)
                self.assertNotIn(":latest", image)
                self.assertNotIn("@sha256:", image)
                self.assertNotIn("Floating :latest", image)

    def test_floating_latest_comments_removed(self) -> None:
        text = COMPOSE.read_text(encoding="utf-8")
        self.assertNotIn("Floating :latest is intentional", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run tests — expect FAIL on current Compose**

```bash
.venv/bin/python -m unittest scripts.tests.test_emhass_image_pin scripts.tests.test_compose_image_pins -v
```

Expected: FAIL — Compose still has `:latest` and/or digest comments; pin strings do not match.

- [ ] **Step 4: Commit only if user asked** (otherwise skip)

Suggested message:

```text
test: expect version-only compose image pins
```

---

### Task 2: Pin Compose + align verify wrapper and docs

**Files:**
- Modify: `docker-compose.yml`
- Modify: `scripts/verify-emhass-load-gap-contract.sh`
- Modify: `docs/EMHASS_UPGRADES.md`
- Modify: `AGENTS.md` (EMHASS upgrades bullets)
- Modify: `memory-bank/techContext.md`

**Interfaces:**
- Consumes: Phase 1 version table above; Task 1 tests
- Produces: version-only Compose; wrapper that requires `version@sha256:digest` when Compose has no digest

- [ ] **Step 1: Update Compose pins**

In `docker-compose.yml`, set:

```yaml
  mosquitto:
    image: eclipse-mosquitto:2.1.2

  mqtt4dsmr:
    image: ghcr.io/antonijn/mqtt4dsmr:v0.9.7

  sigenergy2mqtt:
    image: ghcr.io/seud0nym/sigenergy2mqtt:2026.7.4
    # (remove floating :latest comment)

  emhass:
    image: ghcr.io/davidusb-geek/emhass:v0.17.9
    # (remove floating :latest comment)
```

Leave `wud` digest pin unchanged. Keep `emhass-cron` / `energy-monitor` `wud.watch: false`.

- [ ] **Step 2: Update verify wrapper for version-only Compose**

Keep the existing digest regex on the **candidate** `image` variable. After `image="${1:-${compose_image}}"`, ensure failure message stays clear. Add an explicit branch when no CLI arg and compose image has no `@sha256:`:

```bash
compose_image="$( ... existing python ... )"
if [[ "${#}" -lt 1 ]]; then
  if [[ "${compose_image}" != *@sha256:* ]]; then
    echo "Compose EMHASS image is version-only (${compose_image})." >&2
    echo "Pass a candidate: $0 '<tag@sha256:digest>'" >&2
    exit 2
  fi
fi
image="${1:-${compose_image}}"
```

Keep the existing regex check and `:latest@` rejection unchanged.

- [ ] **Step 3: Update `docs/EMHASS_UPGRADES.md`**

Replace the opening with:

```markdown
# EMHASS upgrades

EMHASS is pinned by release version in `docker-compose.yml` (for example
`ghcr.io/davidusb-geek/emhass:v0.17.9`). Never use `latest`. Do not put a
digest in Compose.

For every candidate:

1. Resolve and record the candidate's release version, immutable manifest
   digest, package version, and source revision.
2. Run:
   `./scripts/verify-emhass-load-gap-contract.sh '<version@sha256:digest>'`
   (always pass version@digest; Compose alone is not enough).
3. Run:
   `.venv/bin/python -m unittest scripts.tests.test_emhass_image_pin scripts.tests.test_compose_image_pins -v`
   (update expected versions in those tests when bumping).
4. Run the complete cron test suite.
5. Commit a note under `docs/verification/emhass/` containing the exact
   candidate, command, and passing output.
6. Only then change the Compose **version** pin and deploy.
```

- [ ] **Step 4: Update `AGENTS.md` EMHASS upgrades section**

Change the bullets that require tag+digest **in Compose** to:

```markdown
- `docker-compose.yml` must use a fixed EMHASS release version (never `latest`).
  Do not put a digest in Compose.
- Before changing the EMHASS image, run
  `./scripts/verify-emhass-load-gap-contract.sh '<version@sha256:digest>'`,
  the Compose pin tests, and the full cron tests.
```

- [ ] **Step 5: Update `memory-bank/techContext.md`**

In the containers table / EMHASS invariants: replace floating `:latest` language with the four version pins from the Phase 1 table. Note WUD notify-only upgrade loop.

- [ ] **Step 6: Run pin tests — expect PASS**

```bash
.venv/bin/python -m unittest scripts.tests.test_emhass_image_pin scripts.tests.test_compose_image_pins -v
```

Expected: PASS.

- [ ] **Step 7: Confirm wrapper rejects no-arg with version-only Compose**

```bash
./scripts/verify-emhass-load-gap-contract.sh ; echo exit:$?
```

Expected: exit `2`, message that Compose is version-only and to pass `tag@sha256:digest`.

- [ ] **Step 8: Commit only if user asked**

Suggested message:

```text
fix: pin energy images to release versions
```

---

### Task 3: Deploy Phase 1 pins (lock running stack)

**Files:**
- No further code edits; deploy existing Compose

**Interfaces:**
- Consumes: Task 2 Compose pins matching live OCI versions
- Produces: remote Compose matches git pins; containers still healthy

- [ ] **Step 1: Operator approval gate**

Ask the operator to approve energy-host deploy for Phase 1. Do not deploy without explicit OK.

- [ ] **Step 2: Deploy energy stack**

```bash
source scripts/lib/source-site-env.sh
./scripts/deploy-energy-stack.sh
```

Expected: rsync + compose up succeeds; `emhass` healthy; cron/monitor recreate if needed.

- [ ] **Step 3: Verify remote image refs**

```bash
source scripts/lib/source-site-env.sh
ssh "${ENERGY_REMOTE_HOST}" "cd ${ENERGY_REMOTE_DIR} && docker compose --env-file config/wud.env ps && docker inspect emhass sigenergy2mqtt mosquitto mqtt4dsmr --format '{{.Name}} {{.Config.Image}}'"
```

Expected: Config.Image shows the four version pins (not `:latest`).

- [ ] **Step 4: Smoke**

- `emhass` healthcheck healthy
- One recent / next MPC path not broken by recreate (check `last_enever_run.json` or wait for natural `:00`/`:30`)
- `sigenergy2mqtt` / mosquitto / mqtt4dsmr running

- [ ] **Step 5: Update `memory-bank/activeContext.md`**

Short section: Phase 1 version pins deployed; date; versions table; Phase 2 changelog review next.

---

### Task 4: Phase 2 — resolve newer versions + changelog review

**Files:**
- Create (only if bumping EMHASS): `docs/verification/emhass/<version>-load-gap-contract.md`
- Modify (only after approved bumps): `docker-compose.yml`, pin test expected strings

**Interfaces:**
- Consumes: WUD API + upstream release pages
- Produces: written changelog verdict per service; bump list for Task 5

- [ ] **Step 1: List registry / GitHub newer tags than Phase 1 pins**

Use WUD API and upstream releases (prefer release notes over digest-only `latest`):

```bash
curl -sS "http://${ENERGY_HOST_IP}:3000/api/containers" | python3 -m json.tool | less
```

Resolve concrete newer **version tags** (not only digests) from:

- EMHASS: https://github.com/davidusb-geek/emhass/releases
- sigenergy2mqtt: https://github.com/seud0nym/sigenergy2mqtt/releases
- mosquitto: https://github.com/eclipse-mosquitto/mosquitto/releases (or Docker Hub tags for `eclipse-mosquitto`)
- mqtt4dsmr: https://github.com/antonijn/mqtt4dsmr/releases (or GHCR tags)

Record a table: current pin → candidate version → link to notes.

- [ ] **Step 2: Changelog break-risk review**

For each candidate, read release notes for:

| Service | Break signals |
|---------|----------------|
| EMHASS | load history / naive / API / config keys / Python deps |
| sigenergy2mqtt | Modbus/MQTT schema, env vars, health, PyModbus |
| mosquitto | config syntax, listener defaults, MQTT protocol |
| mqtt4dsmr | serial device, MQTT topic layout |

Write a short verdict per service: **bump** / **skip** / **blocked** with one-line reason. Present to operator before any Compose edit.

- [ ] **Step 3: EMHASS probe (only if bumping EMHASS)**

Resolve candidate manifest digest for `linux/arm64`, then:

```bash
./scripts/verify-emhass-load-gap-contract.sh 'ghcr.io/davidusb-geek/emhass:<version>@sha256:<digest>'
.venv/bin/python -m unittest discover -s emhass-cron/tests -v
```

Save passing output under `docs/verification/emhass/`.

- [ ] **Step 4: Stop for operator approval of bump list**

Do not edit Compose for Phase 2 until the operator confirms which services to bump.

---

### Task 5: Apply approved bumps + deploy

**Files:**
- Modify: `docker-compose.yml` (approved versions only)
- Modify: `scripts/tests/test_emhass_image_pin.py` and `scripts/tests/test_compose_image_pins.py` expected strings
- Modify: `memory-bank/activeContext.md`

**Interfaces:**
- Consumes: Task 4 approved bump list + EMHASS evidence if applicable
- Produces: deployed newer version pins

- [ ] **Step 1: Update expected pins in tests, then Compose**

TDD: change `EXPECTED` / `PINNED_IMAGE` to new versions → run tests (FAIL) → edit Compose → tests PASS.

- [ ] **Step 2: Deploy with operator OK**

```bash
source scripts/lib/source-site-env.sh
./scripts/deploy-energy-stack.sh
```

- [ ] **Step 3: Post-deploy smoke**

Same as Task 3 Step 4, plus service-specific checks from the changelog review.

- [ ] **Step 4: Update activeContext with bumped versions and date**

- [ ] **Step 5: Commit only if user asked**

Suggested message:

```text
chore: bump pinned energy image versions
```

---

## Spec coverage check

| Spec item | Task |
|-----------|------|
| Pin all four to fixed versions | Task 2 |
| No digest in Compose | Tasks 1–2 |
| Remove floating-latest comments | Task 2 |
| Align pin test + EMHASS_UPGRADES + AGENTS | Tasks 1–2 |
| Probe still uses version@digest CLI | Task 2 |
| Phase 1 lock current stack + deploy | Task 3 |
| Changelog review before upgrade | Task 4 |
| WUD notify-only / API if copy fails | Task 4 |
| Bump + deploy after approval | Task 5 |
| memory-bank updates | Tasks 2–3, 5 |

## Placeholder scan

No TBD/TODO left; Phase 1 versions are concrete; Phase 2 candidates are resolved at execute time via listed URLs/commands.
