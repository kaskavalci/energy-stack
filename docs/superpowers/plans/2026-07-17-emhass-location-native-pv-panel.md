# EMHASS Location and Native PV Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make EMHASS/Open-Meteo use the Home Assistant site's real location and replace the misleading PV history card with one dependency-free dashboard item containing compact actual history and today's forecast.

**Architecture:** `config/site.env` becomes the site-specific source for EMHASS `LAT`, `LON`, `ALT`, `TIME_ZONE`, and the container `TZ`; a local preflight rejects invalid values before rsync or container recreation. The HA dashboard uses an hourly native `statistics-graph` plus a Jinja-rendered forecast table inside one `vertical-stack`, avoiding custom JavaScript and extra backend services.

**Tech Stack:** Bash, Python 3 standard library, Docker Compose, EMHASS, Home Assistant YAML/Jinja, `unittest`, `ruamel.yaml` for render validation.

## Global Constraints

- Leave `input_boolean.emhass_auto_control_master` enabled; do not call its turn-off service.
- Do not install ApexCharts Card or any custom Lovelace resource.
- Do not change `optimization_time_step: 30`, `prediction_horizon: 32`, CSV price methods, `weather_forecast_method: open-meteo`, or `open_meteo_cache_max_age: 120`.
- A price-fetch failure must continue to abort MPC; do not introduce stale-price fallback.
- Force weather recovery through the existing `clear_weather_cache_files()` path before `weather-forecast-cache`.
- Keep exact site coordinates only in gitignored `config/site.env`; tracked examples, docs, tests, and plans must not contain them.
- Preserve unrelated existing changes in `README.md` and `docs/superpowers/plans/`.
- Do not create a git commit unless the user explicitly requests one; review checkpoints replace the skill template's commit steps.

---

## File map

- Create `scripts/lib/validate_site_location.py`: pure location validation plus a CLI used by deploy.
- Create `scripts/tests/test_site_location.py`: validator, Compose wiring, and deploy-recreation regression tests.
- Modify `config/site.env.example`: document required location/timezone keys without real coordinates.
- Modify gitignored `config/site.env`: add the exact values already read from live HA `/api/config`.
- Modify `docker-compose.yml`: load `config/site.env` into EMHASS and remove hard-coded timezone overrides.
- Modify `scripts/deploy-energy-stack.sh`: run location preflight and guarantee EMHASS recreation.
- Create `scripts/tests/test_emhass_trading_dashboard.py`: native PV panel structural regressions.
- Modify `home-assistant/dashboards/emhass_trading.yaml`: native two-part actual/forecast PV item.
- Modify `docs/SETUP.md`: location configuration and deployment behavior.
- Modify `memory-bank/activeContext.md` and `memory-bank/techContext.md`: record verified live state and the new invariant.

---

### Task 1: Fail-fast site-location validation

**Files:**
- Create: `scripts/lib/validate_site_location.py`
- Create: `scripts/tests/test_site_location.py`

**Interfaces:**
- Consumes: a mapping containing `LAT`, `LON`, `ALT`, `TIME_ZONE`, and `TZ`.
- Produces: `validate_site_location(env: Mapping[str, str]) -> list[str]`; CLI exit `0` for valid input and `1` with one error per line for invalid input.

- [ ] **Step 1: Write the failing validator tests**

Create `scripts/tests/test_site_location.py` with the validator cases first:

```python
"""Regression tests for EMHASS site location configuration."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from validate_site_location import validate_site_location  # noqa: E402


VALID_ENV = {
    "LAT": "52.0",
    "LON": "5.0",
    "ALT": "0",
    "TIME_ZONE": "Europe/Amsterdam",
    "TZ": "Europe/Amsterdam",
}


class SiteLocationValidationTests(unittest.TestCase):
    def test_valid_location_has_no_errors(self) -> None:
        self.assertEqual(validate_site_location(VALID_ENV), [])

    def test_missing_values_are_rejected(self) -> None:
        env = dict(VALID_ENV)
        del env["LAT"]
        self.assertIn("LAT is required", validate_site_location(env))

    def test_example_placeholders_are_rejected(self) -> None:
        env = dict(VALID_ENV, LON="CHANGE_ME")
        self.assertIn("LON must be numeric", validate_site_location(env))

    def test_non_finite_values_are_rejected(self) -> None:
        env = dict(VALID_ENV, ALT="nan")
        self.assertIn("ALT must be finite", validate_site_location(env))

    def test_out_of_range_values_are_rejected(self) -> None:
        env = dict(VALID_ENV, LAT="91", LON="-181", ALT="10001")
        errors = validate_site_location(env)
        self.assertIn("LAT must be between -90 and 90", errors)
        self.assertIn("LON must be between -180 and 180", errors)
        self.assertIn("ALT must be between -500 and 10000", errors)

    def test_unknown_timezone_is_rejected(self) -> None:
        env = dict(VALID_ENV, TIME_ZONE="Europe/Not_A_Zone", TZ="Europe/Not_A_Zone")
        self.assertIn(
            "TIME_ZONE must name an installed IANA timezone",
            validate_site_location(env),
        )

    def test_container_timezones_must_match(self) -> None:
        env = dict(VALID_ENV, TZ="UTC")
        self.assertIn("TZ must equal TIME_ZONE", validate_site_location(env))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and confirm the missing module failure**

Run:

```bash
.venv/bin/python -m unittest scripts.tests.test_site_location -v
```

Expected: import failure containing `No module named 'validate_site_location'`.

- [ ] **Step 3: Implement the minimal validator and CLI**

Create `scripts/lib/validate_site_location.py`:

```python
#!/usr/bin/env python3
"""Validate site-specific coordinates before deploying EMHASS."""

from __future__ import annotations

import math
import os
import sys
from collections.abc import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


BOUNDS = {
    "LAT": (-90.0, 90.0),
    "LON": (-180.0, 180.0),
    "ALT": (-500.0, 10000.0),
}


def validate_site_location(env: Mapping[str, str]) -> list[str]:
    errors: list[str] = []
    for name, (lower, upper) in BOUNDS.items():
        raw = env.get(name, "").strip()
        if not raw:
            errors.append(f"{name} is required")
            continue
        try:
            value = float(raw)
        except ValueError:
            errors.append(f"{name} must be numeric")
            continue
        if not math.isfinite(value):
            errors.append(f"{name} must be finite")
        elif not lower <= value <= upper:
            errors.append(f"{name} must be between {lower:g} and {upper:g}")

    timezone = env.get("TIME_ZONE", "").strip()
    if not timezone:
        errors.append("TIME_ZONE is required")
    else:
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            errors.append("TIME_ZONE must name an installed IANA timezone")

    tz = env.get("TZ", "").strip()
    if not tz:
        errors.append("TZ is required")
    elif timezone and tz != timezone:
        errors.append("TZ must equal TIME_ZONE")
    return errors


def main() -> int:
    errors = validate_site_location(os.environ)
    for error in errors:
        print(f"Invalid EMHASS site location: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the focused tests and confirm they pass**

Run:

```bash
.venv/bin/python -m unittest scripts.tests.test_site_location.SiteLocationValidationTests -v
```

Expected: seven tests pass.

- [ ] **Step 5: Review checkpoint**

Confirm the validator uses only the Python standard library, has no network access, and returns all errors in one run. Do not commit.

---

### Task 2: Wire the validated location into EMHASS deployment

**Files:**
- Modify: `scripts/tests/test_site_location.py`
- Modify: `config/site.env.example`
- Modify: gitignored `config/site.env`
- Modify: `docker-compose.yml`
- Modify: `scripts/deploy-energy-stack.sh`

**Interfaces:**
- Consumes: `validate_site_location.py` CLI and the five location/timezone variables in `config/site.env`.
- Produces: an EMHASS container environment with `LAT`, `LON`, `ALT`, `TIME_ZONE`, and `TZ`; every energy deploy recreates EMHASS after local validation.

- [ ] **Step 1: Add failing Compose and deploy tests**

Append to `scripts/tests/test_site_location.py`:

```python
def service_block(text: str, service: str, next_service: str) -> str:
    return text.split(f"  {service}:\n", 1)[1].split(f"  {next_service}:\n", 1)[0]


class SiteLocationWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.deploy = (
            REPO_ROOT / "scripts" / "deploy-energy-stack.sh"
        ).read_text(encoding="utf-8")

    def test_emhass_loads_site_env(self) -> None:
        emhass = service_block(self.compose, "emhass", "emhass-cron")
        self.assertIn("    env_file:\n      - config/site.env\n", emhass)

    def test_compose_has_no_hard_coded_amsterdam_timezone(self) -> None:
        self.assertNotIn("- TZ=Europe/Amsterdam", self.compose)

    def test_deploy_validates_before_rsync(self) -> None:
        validator = self.deploy.index("validate_site_location.py")
        rsync = self.deploy.index("rsync -avz")
        self.assertLess(validator, rsync)

    def test_deploy_force_recreates_emhass(self) -> None:
        self.assertIn("docker compose up -d --force-recreate emhass", self.deploy)
```

- [ ] **Step 2: Run the wiring tests and confirm all four fail**

Run:

```bash
.venv/bin/python -m unittest scripts.tests.test_site_location.SiteLocationWiringTests -v
```

Expected: four assertion failures because EMHASS has no `env_file`, Compose hard-codes `TZ`, deploy has no validator call, and deploy does not force-recreate EMHASS.

- [ ] **Step 3: Add tracked example keys and exact ignored local values**

Insert this section before `# --- Docker compose` in `config/site.env.example`:

```dotenv
# --- EMHASS / Open-Meteo site location ---
# Copy latitude, longitude, elevation, and timezone from HA Settings > System > General.
LAT=CHANGE_ME
LON=CHANGE_ME
ALT=CHANGE_ME
TIME_ZONE=Europe/Amsterdam
```

Keep the existing `TZ=Europe/Amsterdam` line. In gitignored `config/site.env`, add `LAT`, `LON`, and `ALT` using the exact values already captured from live HA `/api/config`, and add `TIME_ZONE=Europe/Amsterdam`. Never echo or copy the exact coordinates into a tracked file.

- [ ] **Step 4: Load site.env in EMHASS and remove timezone overrides**

Change the EMHASS service in `docker-compose.yml` to include:

```yaml
    env_file:
      - config/site.env
```

Keep `LOCAL_COSTFUN=profit`, but delete `- TZ=Europe/Amsterdam` from `emhass`, `emhass-cron`, and `energy-monitor`. Their existing `env_file` declarations will supply `TZ`; EMHASS will additionally consume `LAT`, `LON`, `ALT`, and `TIME_ZONE` by their upstream-supported names.

- [ ] **Step 5: Add deploy preflight and guaranteed EMHASS recreation**

Immediately after sourcing `scripts/lib/source-site-env.sh` in `scripts/deploy-energy-stack.sh`, add:

```bash
PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON}" ]]; then
  PYTHON=python3
fi

echo "Validating EMHASS site location..."
"${PYTHON}" "${ROOT}/scripts/lib/validate_site_location.py"
```

Replace the remote Compose command with two ordered commands so EMHASS is definitely recreated with its new process environment before its dependents are started:

```bash
ssh "${REMOTE_HOST}" "cd ${REMOTE_DIR} && docker compose build emhass-cron energy-monitor && docker compose up -d --force-recreate emhass && docker compose up -d emhass-cron energy-monitor"
```

- [ ] **Step 6: Run focused tests and config validation**

Run:

```bash
.venv/bin/python -m unittest scripts.tests.test_site_location -v
source scripts/lib/source-site-env.sh
.venv/bin/python scripts/lib/validate_site_location.py
docker compose config --quiet
```

Expected: all location tests pass; validator exits `0`; Compose config exits `0` without a missing `config/site.env` error.

- [ ] **Step 7: Review checkpoint**

Inspect `git diff -- docker-compose.yml scripts/deploy-energy-stack.sh config/site.env.example scripts/lib/validate_site_location.py scripts/tests/test_site_location.py`. Confirm no exact coordinate appears and `git status --short` does not list ignored `config/site.env`. Do not commit.

---

### Task 3: Build the low-memory native actual/forecast PV item

**Files:**
- Create: `scripts/tests/test_emhass_trading_dashboard.py`
- Modify: `home-assistant/dashboards/emhass_trading.yaml`

**Interfaces:**
- Consumes: long-term/hourly statistics from `sensor.emhass_pv_power_w` and forecast dictionaries from `sensor.p_pv_forecast.attributes.forecasts` with `date` and `p_pv_forecast` fields.
- Produces: one native `vertical-stack` layout item; first card is a two-day hourly actual-PV graph and second is a today-only forecast table.

- [ ] **Step 1: Write the failing dashboard regression tests**

Create `scripts/tests/test_emhass_trading_dashboard.py`:

```python
"""Structural regression tests for the EMHASS trading dashboard."""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = REPO_ROOT / "home-assistant" / "dashboards" / "emhass_trading.yaml"


def pv_panel() -> str:
    text = DASHBOARD.read_text(encoding="utf-8")
    return text.split("# PV actual and forecast", 1)[1].split(
        "title: Import price", 1
    )[0]


class NativePvPanelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = pv_panel()

    def test_panel_uses_only_native_cards(self) -> None:
        self.assertIn("type: vertical-stack", self.panel)
        self.assertNotIn("custom:", self.panel)

    def test_actual_history_uses_compact_hourly_statistics(self) -> None:
        self.assertIn("type: statistics-graph", self.panel)
        self.assertIn("days_to_show: 2", self.panel)
        self.assertIn("period: hour", self.panel)
        self.assertIn("stat_types:\n              - mean", self.panel)
        self.assertIn("entity: sensor.emhass_pv_power_w", self.panel)

    def test_forecast_table_is_filtered_to_today(self) -> None:
        self.assertIn("entity_id:\n              - sensor.p_pv_forecast", self.panel)
        self.assertIn("now().strftime('%Y-%m-%d')", self.panel)
        self.assertIn("row.date", self.panel)
        self.assertIn("row.p_pv_forecast", self.panel)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the dashboard tests and confirm the missing-panel failure**

Run:

```bash
.venv/bin/python -m unittest scripts.tests.test_emhass_trading_dashboard -v
```

Expected: three errors from `IndexError` because the `# PV actual and forecast` marker does not exist yet.

- [ ] **Step 3: Replace the old PV history graph with the native stack**

Replace the `PV forecast vs Envoy (24 h)` history card in `home-assistant/dashboards/emhass_trading.yaml` with:

```yaml
      # PV actual and forecast
      - type: vertical-stack
        cards:
          - type: statistics-graph
            title: Actual PV — yesterday and today
            days_to_show: 2
            period: hour
            chart_type: line
            stat_types:
              - mean
            entities:
              - entity: sensor.emhass_pv_power_w
                name: Actual PV (hourly mean)

          - type: markdown
            title: Today's PV forecast
            entity_id:
              - sensor.p_pv_forecast
            content: >
              {%- set rows = state_attr('sensor.p_pv_forecast', 'forecasts') or [] -%}
              {%- set today = now().strftime('%Y-%m-%d') -%}
              {%- set ns = namespace(count=0) -%}
              {%- for row in rows -%}
              {%- if (row.date | string)[:10] == today -%}
              {%- set ns.count = ns.count + 1 -%}
              {%- endif -%}
              {%- endfor -%}
              {%- if ns.count == 0 -%}
              <p><em>No PV forecast rows for today.</em></p>
              {%- else -%}
              <table width="100%">
              <thead><tr><th>Time</th><th align="right">Forecast</th></tr></thead>
              <tbody>
              {%- for row in rows -%}
              {%- if (row.date | string)[:10] == today -%}
              <tr><td>{{ (row.date | string)[11:16] }}</td><td align="right">{{ row.p_pv_forecast | float(0) | round(0) }} W</td></tr>
              {%- endif -%}
              {%- endfor -%}
              </tbody>
              </table>
              {%- endif -%}
```

- [ ] **Step 4: Run the dashboard tests and render the package**

Run:

```bash
.venv/bin/python -m unittest scripts.tests.test_emhass_trading_dashboard -v
./scripts/render-ha-packages.sh
.venv/bin/python - <<'PY'
from pathlib import Path
from ruamel.yaml import YAML

path = Path('.rendered/home-assistant/dashboards/emhass_trading.yaml')
with path.open(encoding='utf-8') as stream:
    YAML(typ='safe').load(stream)
print('rendered dashboard YAML: OK')
PY
```

Expected: three tests pass, render succeeds, and parser prints `rendered dashboard YAML: OK`.

- [ ] **Step 5: Review checkpoint**

Confirm the PV panel contains no `custom:` card, no ApexCharts resource, and only one historical entity sampled at hourly resolution. Do not commit.

---

### Task 4: Document the configuration contract

**Files:**
- Modify: `docs/SETUP.md`
- Modify: `memory-bank/techContext.md`

**Interfaces:**
- Consumes: the implemented environment and dashboard contract from Tasks 2–3.
- Produces: setup instructions and an invariant future agents can follow.

- [ ] **Step 1: Update setup documentation**

In `docs/SETUP.md`, extend the `config/site.env` purpose to include Open-Meteo location and add a section stating:

```markdown
## EMHASS / Open-Meteo location

Set `LAT`, `LON`, `ALT`, `TIME_ZONE`, and `TZ` in `config/site.env`. Copy the
coordinates and elevation from Home Assistant under **Settings → System →
General**. `TIME_ZONE` and `TZ` must be the same IANA timezone, for example
`Europe/Amsterdam`.

`deploy-energy-stack.sh` validates these values before uploading anything and
recreates EMHASS so cached parameters cannot retain the container image's
default location.
```

Also change the Docker Compose paragraph to say `emhass`, `emhass-cron`, and `energy-monitor` load site variables from `config/site.env`.

- [ ] **Step 2: Record the new technical invariants**

Add these bullets under EMHASS invariants in `memory-bank/techContext.md`:

```markdown
- EMHASS site location comes from validated `LAT`, `LON`, `ALT`, and
  `TIME_ZONE` in gitignored `config/site.env`; never rely on image defaults.
- `TZ` and `TIME_ZONE` must match.
- The trading dashboard uses native hourly statistics plus a today-only PV
  forecast table; do not add custom chart resources to the memory-constrained
  HA host.
```

- [ ] **Step 3: Run a tracked-secret and placeholder review**

Run:

```bash
git diff --check
git diff -- config/site.env.example docs/SETUP.md memory-bank/techContext.md
```

Expected: `git diff --check` exits `0`; the tracked diff contains generic example values only and no site-specific coordinates.

- [ ] **Step 4: Review checkpoint**

Confirm docs describe the actual validator and recreation behavior exactly. Do not commit.

---

### Task 5: Full local verification

**Files:**
- Verify all files changed in Tasks 1–4.

**Interfaces:**
- Consumes: completed implementation and local site configuration.
- Produces: evidence that the change is safe to deploy.

- [ ] **Step 1: Run the complete Python regression suite**

Run:

```bash
.venv/bin/python -m unittest discover -s scripts/tests -v
```

Expected: every existing and new test passes with no errors or failures.

- [ ] **Step 2: Run shell and Compose checks**

Run:

```bash
bash -n scripts/deploy-energy-stack.sh scripts/setup-local.sh scripts/render-ha-packages.sh
source scripts/lib/source-site-env.sh
.venv/bin/python scripts/lib/validate_site_location.py
docker compose config --quiet
git diff --check
```

Expected: all five commands exit `0`.

- [ ] **Step 3: Render and parse every HA YAML file**

Run:

```bash
./scripts/render-ha-packages.sh
.venv/bin/python - <<'PY'
from pathlib import Path
from ruamel.yaml import YAML

yaml = YAML(typ='base')
for path in sorted(Path('.rendered/home-assistant').rglob('*.yaml')):
    with path.open(encoding='utf-8') as stream:
        yaml.load(stream)
    print(path)
PY
```

Expected: every rendered YAML path prints and the command exits `0`.

- [ ] **Step 4: Review the complete diff without disturbing unrelated work**

Run:

```bash
git status --short
git diff -- docker-compose.yml config/site.env.example scripts/deploy-energy-stack.sh scripts/lib/validate_site_location.py scripts/tests/test_site_location.py scripts/tests/test_emhass_trading_dashboard.py home-assistant/dashboards/emhass_trading.yaml docs/SETUP.md memory-bank/techContext.md
```

Expected: only intended task files appear in the scoped diff; pre-existing `README.md` and other untracked plans remain untouched.

- [ ] **Step 5: Review checkpoint**

Record the exact passing test count and do not claim deployment success yet. Do not commit.

---

### Task 6: Deploy location fix and verify live MPC

**Files:**
- Deploy via: `scripts/deploy-energy-stack.sh`
- Read live: EMHASS environment, `/data/params.pkl`, `/data/cached-open-meteo-forecast-b.json`, `/share/last_enever_run.json`

**Interfaces:**
- Consumes: validated site.env and energy-stack deployment.
- Produces: a live EMHASS instance using the Dutch site rather than the image's Alpine defaults, with a successful fresh MPC publish.

- [ ] **Step 1: Confirm the master is on without changing it**

Use the existing HA API credentials to read `input_boolean.emhass_auto_control_master` and record that its state is `on`. Do not issue any service call against the entity.

- [ ] **Step 2: Deploy the energy stack**

Run:

```bash
./scripts/deploy-energy-stack.sh
```

Expected: location validation passes before rsync; EMHASS is recreated; `emhass`, `emhass-cron`, and `energy-monitor` end in `Up`/healthy state.

- [ ] **Step 3: Force fresh weather and MPC through the existing safe path**

Run:

```bash
source config/site.env
ssh "$ENERGY_REMOTE_HOST" "cd '$ENERGY_REMOTE_DIR' && docker compose exec -T emhass-cron python3 /app/fetch_prices_emhass.py --action mpc --force-weather -v"
```

Expected: logs show forced cache refresh, successful `weather-forecast-cache`, `naive-mpc-optim`, and `publish-data`; exit code `0`.

- [ ] **Step 4: Verify cached EMHASS parameters match its environment**

Run:

```bash
source config/site.env
ssh "$ENERGY_REMOTE_HOST" "cd '$ENERGY_REMOTE_DIR' && docker compose exec -T emhass python3 - <<'PY'
import os
import pickle

_, params = pickle.load(open('/data/params.pkl', 'rb'))
conf = params['retrieve_hass_conf']
assert abs(float(conf['Latitude']) - float(os.environ['LAT'])) < 1e-6
assert abs(float(conf['Longitude']) - float(os.environ['LON'])) < 1e-6
assert abs(float(conf['Altitude']) - float(os.environ['ALT'])) < 1e-6
assert conf['time_zone'] == os.environ['TIME_ZONE']
print('EMHASS parameters match configured site location')
PY"
```

Expected: `EMHASS parameters match configured site location`.

- [ ] **Step 5: Verify the fresh Open-Meteo response uses the configured region**

Run:

```bash
source config/site.env
ssh "$ENERGY_REMOTE_HOST" "cd '$ENERGY_REMOTE_DIR' && docker compose exec -T emhass python3 - <<'PY'
import json
import os

cache = json.load(open('/data/cached-open-meteo-forecast-b.json'))
assert abs(float(cache['latitude']) - float(os.environ['LAT'])) < 0.1
assert abs(float(cache['longitude']) - float(os.environ['LON'])) < 0.1
assert cache['timezone'] == os.environ['TIME_ZONE']
print('Open-Meteo cache matches configured site region')
PY"
```

Expected: `Open-Meteo cache matches configured site region`. The returned elevation may differ because Open-Meteo uses its own terrain grid, so do not assert elevation equality.

- [ ] **Step 6: Verify MPC and master state**

Read `/share/last_enever_run.json` and confirm `mpc.ok` is true with a current timestamp. Read `sensor.p_pv_forecast` and confirm today's `forecasts` rows exist. Re-read `input_boolean.emhass_auto_control_master` and confirm it is still `on`.

- [ ] **Step 7: Review checkpoint**

If location assertions or MPC fail, stop before the HA dashboard deployment, leave the master unchanged, and preserve the logs. Do not commit.

---

### Task 7: Deploy and verify the native HA panel

**Files:**
- Deploy via: `scripts/deploy-home-assistant.sh`
- Modify after verification: `memory-bank/activeContext.md`

**Interfaces:**
- Consumes: rendered native dashboard and successful EMHASS forecast publication.
- Produces: live combined actual/forecast PV item and an accurate operator handoff record.

- [ ] **Step 1: Deploy the HA packages and dashboard**

Run:

```bash
./scripts/deploy-home-assistant.sh
```

Expected: render succeeds, `ha core check` succeeds, HA restarts, and the deploy prints its backup path.

- [ ] **Step 2: Verify the live dashboard source is dependency-free**

Run:

```bash
source config/site.env
ssh -p "$HA_SSH_PORT" "$HA_SSH_USER@$HA_SSH_HOST" \
  "sed -n '/# PV actual and forecast/,/title: Import price/p' /mnt/data/supervisor/homeassistant/dashboards/emhass_trading.yaml"
```

Expected: output contains `vertical-stack`, `statistics-graph`, `days_to_show: 2`, `period: hour`, and the today-filtered Markdown table; output contains no `custom:`.

- [ ] **Step 3: Check HA health after restart**

Run:

```bash
source config/site.env
ssh -p "$HA_SSH_PORT" "$HA_SSH_USER@$HA_SSH_HOST" "ha core info && ha core check"
```

Expected: Core reports running and config check succeeds.

- [ ] **Step 4: Update active context with observed facts**

Add a dated `2026-07-17` section at the top of `memory-bank/activeContext.md` recording only verified results:

```markdown
## EMHASS location and native PV panel verified (2026-07-17)

- EMHASS parameters and fresh Open-Meteo cache match the configured Dutch site
  and `Europe/Amsterdam`; Alpine image defaults are no longer in use.
- Forced weather refresh, MPC optimization, and publish completed successfully.
- The EMHASS master remained enabled throughout deployment and recovery.
- The PV dashboard item uses native two-day hourly actual statistics plus a
  today-only forecast table; no custom frontend resource was installed.
```

If any bullet is not verified, replace it with the observed failure and next diagnostic step instead of claiming success.

- [ ] **Step 5: Run final verification after the memory-bank edit**

Run:

```bash
.venv/bin/python -m unittest discover -s scripts/tests -v
git diff --check
git status --short
```

Expected: all tests pass, diff check exits `0`, and status contains only intended changes plus the user's pre-existing work.

- [ ] **Step 6: Final review checkpoint**

Report the actual MPC timestamp/status, the coordinate-match assertions, HA core check, test count, and that the master remained on. Do not commit.
