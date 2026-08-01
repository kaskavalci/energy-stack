# Open-Meteo Proactive Soft Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh Open-Meteo cache at soft age `max_age − 2×step` (90 with defaults) while keeping hard stale/B5 failure at `open_meteo_cache_max_age` 150.

**Architecture:** Extend `lib/weather_cache_stale.py` with a soft `weather_cache_refresh_reason()` used only to decide clear+fetch in cron. Keep `weather_cache_stale_reason()` as the hard gate for post-refresh failure and monitor B5. Raise config/default max age from 120 to 150.

**Tech Stack:** Python 3 stdlib, `unittest`, shared `lib/` mounted into `emhass-cron` and `energy-monitor` images.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-01-open-meteo-proactive-refresh-design.md`
- Soft JSON age: `>= open_meteo_cache_max_age − 2×optimization_time_step`
- Hard JSON age: `>= open_meteo_cache_max_age` (config **150**)
- Monitor B5 must keep calling hard `weather_cache_stale_reason` only
- Soft clear still uses `clear_weather_cache_files()` before `weather-forecast-cache`
- Post-check / retry / raise in `ensure_weather_forecast_cache` uses **hard** only
- Do not add a second config key for soft age
- Keep `optimization_time_step` 30; price CSV; weather `open-meteo`
- Create implementation commits only when the user explicitly authorizes commits; otherwise leave diffs unstaged and report the suggested message
- Read `memory-bank/activeContext.md`, `memory-bank/techContext.md`, and `.agents/skills/emhass-energy-stack/SKILL.md` before live deploy work
- Source `config/site.env` before SSH/deploy; never hardcode hosts or tokens

## File Map

| File | Responsibility |
|------|----------------|
| `lib/weather_cache_stale.py` | Soft refresh reason, hard stale reason, default max age 150, validation |
| `lib/tests/test_weather_cache_stale.py` | Unit tests for soft/hard thresholds and validation |
| `emhass-cron/fetch_prices_emhass.py` | Clear on soft; post-validate on hard; refresh-weather skip on soft |
| `emhass-cron/tests/test_fetch_prices_emhass.py` | Wiring tests for ensure + refresh-weather; example config 150 |
| `emhass/share/config.json.example` | `open_meteo_cache_max_age: 150` |
| `AGENTS.md` | Invariant: hard 150; soft `max_age − 2×step` |
| `.agents/skills/emhass-energy-stack/SKILL.md` | Same invariant table/pitfall update |
| `memory-bank/techContext.md` | Same |
| `memory-bank/activeContext.md` | Note when deployed (after live verify) |

---

### Task 1: Soft/hard helpers in shared weather cache lib

**Files:**
- Modify: `lib/weather_cache_stale.py`
- Create: `lib/tests/test_weather_cache_stale.py`

**Interfaces:**
- Consumes: existing `read_open_meteo_cache_max_age_min`, `read_optimization_time_step_min`, `open_meteo_json_age_min`, `inspect_weather_cache_bounds`, `docker_available`
- Produces:
  - `DEFAULT_OPEN_METEO_CACHE_MAX_AGE_MIN: int = 150`
  - `read_open_meteo_refresh_age_min(share_dir: Path) -> int`
  - `weather_cache_refresh_reason(share_dir: Path, prediction_horizon: int, *, force: bool = False) -> str | None`
  - `weather_cache_stale_reason(...)` unchanged hard semantics (limit from config/default 150)
  - `validate_cache_max_age_config(share_dir: Path) -> str | None` also rejects soft `<= step`

- [ ] **Step 1: Write failing unit tests**

Create `lib/tests/test_weather_cache_stale.py`:

```python
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "lib"))

import weather_cache_stale as wcs  # noqa: E402


class WeatherCacheThresholdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.share = Path(self.tmp.name)
        (self.share / "config.json").write_text(
            json.dumps(
                {
                    "open_meteo_cache_max_age": 150,
                    "optimization_time_step": 30,
                    "prediction_horizon": 32,
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _bounds_ok(self):
        tz = ZoneInfo("Europe/Amsterdam")
        now = datetime.now(tz)
        start = now - timedelta(minutes=30)
        end = now + timedelta(hours=48)
        return start, end

    def test_default_max_age_is_150(self):
        self.assertEqual(wcs.DEFAULT_OPEN_METEO_CACHE_MAX_AGE_MIN, 150)

    def test_refresh_age_is_max_age_minus_two_steps(self):
        self.assertEqual(wcs.read_open_meteo_refresh_age_min(self.share), 90)

    def test_soft_triggers_at_refresh_age_not_before(self):
        start, end = self._bounds_ok()
        with (
            patch.object(wcs, "docker_available", return_value=True),
            patch.object(wcs, "inspect_weather_cache_bounds", return_value=(start, end)),
            patch.object(wcs, "open_meteo_json_age_min", return_value=89.0),
        ):
            self.assertIsNone(
                wcs.weather_cache_refresh_reason(self.share, 32, force=False)
            )
        with (
            patch.object(wcs, "docker_available", return_value=True),
            patch.object(wcs, "inspect_weather_cache_bounds", return_value=(start, end)),
            patch.object(wcs, "open_meteo_json_age_min", return_value=90.0),
        ):
            msg = wcs.weather_cache_refresh_reason(self.share, 32, force=False)
            self.assertIsNotNone(msg)
            self.assertIn("refresh at 90", msg)
            self.assertIn("hard limit 150", msg)

    def test_hard_triggers_at_max_age_not_soft(self):
        start, end = self._bounds_ok()
        with (
            patch.object(wcs, "docker_available", return_value=True),
            patch.object(wcs, "inspect_weather_cache_bounds", return_value=(start, end)),
            patch.object(wcs, "open_meteo_json_age_min", return_value=149.0),
        ):
            self.assertIsNone(
                wcs.weather_cache_stale_reason(self.share, 32, force=False)
            )
            soft = wcs.weather_cache_refresh_reason(self.share, 32, force=False)
            self.assertIsNotNone(soft)
        with (
            patch.object(wcs, "docker_available", return_value=True),
            patch.object(wcs, "inspect_weather_cache_bounds", return_value=(start, end)),
            patch.object(wcs, "open_meteo_json_age_min", return_value=150.0),
        ):
            hard = wcs.weather_cache_stale_reason(self.share, 32, force=False)
            self.assertIsNotNone(hard)
            self.assertIn("limit 150", hard)
            self.assertNotIn("refresh at", hard)

    def test_validate_rejects_soft_not_greater_than_step(self):
        (self.share / "config.json").write_text(
            json.dumps(
                {
                    "open_meteo_cache_max_age": 90,
                    "optimization_time_step": 30,
                }
            ),
            encoding="utf-8",
        )
        err = wcs.validate_cache_max_age_config(self.share)
        self.assertIsNotNone(err)
        self.assertIn("refresh", err.lower())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/halil/workspace/misc/energy-stack
PYTHONPATH=lib python3 lib/tests/test_weather_cache_stale.py -v
```

Expected: FAIL on missing `read_open_meteo_refresh_age_min` / `weather_cache_refresh_reason` and/or default still 120.

- [ ] **Step 3: Implement helpers in `lib/weather_cache_stale.py`**

1. Change `DEFAULT_OPEN_METEO_CACHE_MAX_AGE_MIN` to `150`.

2. Add:

```python
def read_open_meteo_refresh_age_min(share_dir: Path) -> int:
    """Soft clear/refetch age: max_age - 2 * optimization_time_step."""
    max_age = read_open_meteo_cache_max_age_min(share_dir)
    step = read_optimization_time_step_min(share_dir)
    return max_age - (2 * step)
```

3. Extend `validate_cache_max_age_config`:

```python
def validate_cache_max_age_config(share_dir: Path) -> str | None:
    max_age = read_open_meteo_cache_max_age_min(share_dir)
    step = read_optimization_time_step_min(share_dir)
    if max_age <= step:
        return (
            f"open_meteo_cache_max_age ({max_age} min) must be > "
            f"optimization_time_step ({step} min)"
        )
    refresh_age = max_age - (2 * step)
    if refresh_age <= step:
        return (
            f"open_meteo refresh age ({refresh_age} min = max_age - 2*step) "
            f"must be > optimization_time_step ({step} min)"
        )
    return None
```

4. Add `weather_cache_refresh_reason` with the same structural (docker/pkl/bounds) checks as hard stale, then soft JSON age:

```python
if json_age is not None and json_age >= refresh_age:
    return (
        f"Open-Meteo JSON is {json_age:.0f} min old "
        f"(refresh at {refresh_age} min; hard limit {max_age} min)"
    )
```

Hard `weather_cache_stale_reason` JSON branch stays:

```python
if json_age is not None and json_age >= max_age:
    return f"Open-Meteo JSON is {json_age:.0f} min old (limit {max_age} min)"
```

Prefer extracting a private structural helper to avoid duplicating pkl/bounds logic. Do not change those rules.

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=lib python3 lib/tests/test_weather_cache_stale.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit (only if user authorized commits)**

```bash
git add lib/weather_cache_stale.py lib/tests/test_weather_cache_stale.py
git commit -m "$(cat <<'EOF'
feat(weather): soft Open-Meteo refresh age vs hard stale limit

Add weather_cache_refresh_reason at max_age-2*step and raise default
hard max age to 150 so cron can clear early while B5 stays hard-only.

EOF
)"
```

---

### Task 2: Wire cron ensure + refresh-weather to soft clear / hard post-check

**Files:**
- Modify: `emhass-cron/fetch_prices_emhass.py`
- Modify: `emhass-cron/tests/test_fetch_prices_emhass.py`

**Interfaces:**
- Consumes: `weather_cache_refresh_reason`, `weather_cache_stale_reason` from `weather_cache_stale`
- Produces: `ensure_weather_forecast_cache` clears on soft; raises only on hard after retry; `refresh-weather` skips when soft is None

- [ ] **Step 1: Write failing wiring tests**

Add to `emhass-cron/tests/test_fetch_prices_emhass.py`:

```python
class EnsureWeatherSoftRefreshTests(unittest.TestCase):
    def test_ensure_clears_on_soft_refresh_reason_posts_hard(self):
        share = Path(tempfile.mkdtemp())
        try:
            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(
                        fetch_prices_emhass,
                        "weather_cache_refresh_reason",
                        side_effect=["soft age", None],
                    )
                )
                stack.enter_context(
                    patch.object(
                        fetch_prices_emhass,
                        "weather_cache_stale_reason",
                        return_value=None,
                    )
                )
                clear = stack.enter_context(
                    patch.object(fetch_prices_emhass, "clear_weather_cache_files")
                )
                stack.enter_context(
                    patch.object(
                        fetch_prices_emhass,
                        "call_emhass_action_with_retry",
                        return_value=None,
                    )
                )
                fetch_prices_emhass.ensure_weather_forecast_cache(
                    "http://emhass:5000",
                    share,
                    32,
                    force=False,
                )
                clear.assert_called_once()
        finally:
            import shutil

            shutil.rmtree(share, ignore_errors=True)

    def test_ensure_does_not_raise_when_hard_ok(self):
        share = Path(tempfile.mkdtemp())
        try:
            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(
                        fetch_prices_emhass,
                        "weather_cache_refresh_reason",
                        return_value=None,
                    )
                )
                stack.enter_context(
                    patch.object(
                        fetch_prices_emhass,
                        "weather_cache_stale_reason",
                        return_value=None,
                    )
                )
                stack.enter_context(
                    patch.object(fetch_prices_emhass, "clear_weather_cache_files")
                )
                stack.enter_context(
                    patch.object(
                        fetch_prices_emhass,
                        "call_emhass_action_with_retry",
                        return_value=None,
                    )
                )
                fetch_prices_emhass.ensure_weather_forecast_cache(
                    "http://emhass:5000",
                    share,
                    32,
                )
        finally:
            import shutil

            shutil.rmtree(share, ignore_errors=True)
```

- [ ] **Step 2: Run the new tests — expect fail**

```bash
cd /Users/halil/workspace/misc/energy-stack
python3 emhass-cron/tests/test_fetch_prices_emhass.py EnsureWeatherSoftRefreshTests -v
```

Expected: FAIL (`weather_cache_refresh_reason` not imported / ensure still uses hard for clear).

- [ ] **Step 3: Implement cron wiring**

Import `weather_cache_refresh_reason`.

Replace clear decision in `ensure_weather_forecast_cache` with soft reason; keep post-check / retry / raise on hard `weather_cache_stale_reason` only.

In `--action refresh-weather`, skip only when soft reason is None:

```python
stale = weather_cache_refresh_reason(args.share_dir, horizon, force=False)
```

Keep dry-run `weather_cache_stale` on **hard** `weather_cache_stale_reason`.

Monitor: **no code change**.

- [ ] **Step 4: Run cron weather + existing suite**

```bash
python3 emhass-cron/tests/test_fetch_prices_emhass.py EnsureWeatherSoftRefreshTests -v
PYTHONPATH=lib python3 lib/tests/test_weather_cache_stale.py -v
python3 emhass-cron/tests/test_fetch_prices_emhass.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit (only if user authorized commits)**

```bash
git add emhass-cron/fetch_prices_emhass.py emhass-cron/tests/test_fetch_prices_emhass.py
git commit -m "$(cat <<'EOF'
feat(cron): clear Open-Meteo cache on soft refresh age

Use weather_cache_refresh_reason for clear/skip and keep hard stale
checks for post-refresh failure so MPC does not abort on soft-only age.

EOF
)"
```

---

### Task 3: Config 150 + docs/invariants

**Files:**
- Modify: `emhass/share/config.json.example`
- Modify: `emhass-cron/tests/test_fetch_prices_emhass.py` (example assertion)
- Modify: `AGENTS.md`
- Modify: `.agents/skills/emhass-energy-stack/SKILL.md`
- Modify: `memory-bank/techContext.md`
- Modify: `memory-bank/activeContext.md` (short note after local verify; live deploy note when done)

**Interfaces:**
- Consumes: Task 1 default 150; Task 2 behavior
- Produces: documented hard 150 + soft `max_age − 2×step`

- [ ] **Step 1: Write failing example-config assertion**

In `EmhassConfigExampleTests`:

```python
def test_open_meteo_cache_max_age_is_150(self):
    config = json.loads(
        (REPO_ROOT / "emhass/share/config.json.example").read_text(
            encoding="utf-8"
        )
    )
    self.assertEqual(config["open_meteo_cache_max_age"], 150)
```

- [ ] **Step 2: Run test — expect fail**

```bash
python3 emhass-cron/tests/test_fetch_prices_emhass.py \
  EmhassConfigExampleTests.test_open_meteo_cache_max_age_is_150 -v
```

Expected: FAIL (`120 != 150`).

- [ ] **Step 3: Update example config and docs**

Set `"open_meteo_cache_max_age": 150` in `emhass/share/config.json.example`.

Update invariants:

- `AGENTS.md`: hard **150**; must stay `>` MPC cadence; soft refresh at `max_age − 2×step` (90 with defaults).
- `.agents/skills/emhass-energy-stack/SKILL.md`: table value **150**; pitfall note soft vs hard.
- `memory-bank/techContext.md`: **150**; soft `max_age − 2×step` for cron clear.

Do **not** commit live `emhass/share/config.json`. Deploy updates live host via existing energy-stack deploy after code lands.

- [ ] **Step 4: Re-run assertions + lib tests**

```bash
python3 emhass-cron/tests/test_fetch_prices_emhass.py EmhassConfigExampleTests -v
PYTHONPATH=lib python3 lib/tests/test_weather_cache_stale.py -v
```

Expected: PASS.

- [ ] **Step 5: Local verify notes in activeContext (no live deploy unless asked)**

Append a short “Open-Meteo soft refresh — implemented locally” bullet to `memory-bank/activeContext.md` with date 2026-08-01. Live deploy + host `config.json` 150 remains an operator step:

```bash
./scripts/deploy-energy-stack.sh
# then confirm open_meteo_cache_max_age is 150 in share/config.json on energy host
```

- [ ] **Step 6: Commit (only if user authorized commits)**

```bash
git add emhass/share/config.json.example emhass-cron/tests/test_fetch_prices_emhass.py \
  AGENTS.md .agents/skills/emhass-energy-stack/SKILL.md \
  memory-bank/techContext.md memory-bank/activeContext.md
git commit -m "$(cat <<'EOF'
docs(config): set open_meteo_cache_max_age to 150

Align example config and agent invariants with soft refresh at
max_age-2*step and hard B5/fail at 150 minutes.

EOF
)"
```

---

## Spec coverage (self-review)

| Spec requirement | Task |
|------------------|------|
| Soft = `max_age − 2×step` | Task 1 |
| Hard = `open_meteo_cache_max_age` 150 | Tasks 1, 3 |
| Soft clear in ensure + refresh-weather | Task 2 |
| Hard post-check / B5 unchanged | Task 2 (cron); monitor untouched |
| No second soft config key | Task 1 derived helper |
| Default constant 150 | Task 1 |
| Validation soft `> step` | Task 1 |
| Example config + docs | Task 3 |
| Tests soft/hard/wiring | Tasks 1–2 |
| Live deploy out of code path | Task 3 note |

## Placeholder / consistency check

- Function names locked: `weather_cache_refresh_reason`, `read_open_meteo_refresh_age_min`
- Soft message must include `refresh at` and `hard limit`
- Hard message keeps `(limit N min)` without `refresh at`
- Dry-run continues to report hard stale only
