# EMHASS Daily Planned Profit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After each successful MPC, lock write-once planned `cost_profit` for tomorrow’s covered slots and show it next to realized profit on the trading dashboard.

**Architecture:** `emhass-cron` reuses one Zonneplan price snapshot per MPC, reads `/data/opt_res_latest.csv` through `docker exec` (stdlib JSON, no pandas in cron), and writes `/share/daily_planned_profit.json`. Home Assistant reads a compact JSON helper; a template sensor shows today’s locked value. `energy-monitor` sends one `notify.notify` if tomorrow is still unlocked after 22:00 local time.

**Tech Stack:** Python 3.12 stdlib in `emhass-cron` / `energy-monitor`; Home Assistant YAML packages + Lovelace `statistics-graph`; `unittest`; `fcntl` file lock; Docker socket already mounted.

**Spec:** `docs/superpowers/specs/2026-08-16-emhass-daily-planned-profit-design.md`

## Global Constraints

- `optimization_time_step` is 30 minutes; `prediction_horizon` is at most 32; do not raise the live MPC horizon
- Price methods stay `csv`; weather stays `open-meteo`; price fetch failure still aborts MPC (no stale CSV lock)
- Metric is EMHASS `cost_profit` as supplied; do not recompute from `P_grid` or use `sensor.total_cost_fun_value`
- Locked value is covered-slot profit, not a full calendar day; `partial` is always `true`
- Write-once per `YYYY-MM-DD` key; never overwrite; prune keys older than 14 local days; never prune future keys
- JSON on `/share` is source of truth; do not rebuild locks from HA recorder
- Planned-profit side effects must not flip a successful MPC to failed; record auxiliary errors in `last_enever_run.json` `planned_profit`
- HA transport is `input_text.emhass_planned_profit_payload` (max 255); do not POST `/api/states/sensor.emhass_planned_profit_today`
- Notify with `notify.notify` only; no Telegram, no WUD, no automatic container updates
- All date math uses `Europe/Amsterdam`
- Do not lock on dry-run, write-only, publish-only, SOC-refresh, weather-only, or failed MPC
- Dockerfile change allowed only to `COPY` new cron Python modules; no compose/mount/volume changes
- Never log HA tokens or commit `config/site.env` / live share JSON
- Create git commits only when the user authorizes them; otherwise leave diffs unstaged and report the suggested message

## File Map

| File | Responsibility |
|------|----------------|
| `emhass-cron/planned_profit.py` | Target-date detection, `cost_profit` grouping, JSON lock, helper payload, docker result read |
| `emhass-cron/fetch_prices_emhass.py` | One price snapshot per MPC; call lock/publish only after successful marker |
| `emhass-cron/tests/test_planned_profit.py` | Unit tests for grouping, persist, payload length, docker reader parsing |
| `emhass-cron/tests/test_fetch_prices_emhass.py` | Snapshot-once MPC path; auxiliary lock failure leaves `mpc.ok=true` |
| `emhass-cron/Dockerfile` | `COPY` `planned_profit.py` next to `fetch_prices_emhass.py` |
| `monitor/health_notify.py` | 22:00 missed-lock path, once-per-target signature |
| `monitor/tests/test_health_notify.py` | Deadline, dedup, late lock, malformed JSON, delivery retry |
| `home-assistant/ha_emhass_helpers.yaml` | `input_text.emhass_planned_profit_payload` |
| `home-assistant/ha_emhass_dashboard.yaml` | `sensor.emhass_planned_profit_today` |
| `home-assistant/dashboards/emhass_trading.yaml` | Prices row + two-series 7-day graph |
| `scripts/tests/test_sigen_ems_package.py` | Sensor/helper structural contract |
| `scripts/tests/test_emhass_trading_dashboard.py` | Prices card + graph structure |
| `.gitignore` | Live JSON + `.lock` |
| `emhass-cron/README.md` | Lock source, schema, helper |
| `docs/ENTITY_CONTRACT.md` | New helper + sensor |
| `memory-bank/activeContext.md` / `techContext.md` | Feature note after tests pass |

---

### Task 1: One Zonneplan price snapshot per MPC

**Files:**
- Modify: `emhass-cron/fetch_prices_emhass.py`
- Modify: `emhass-cron/tests/test_fetch_prices_emhass.py`

**Interfaces:**
- Consumes: existing `fetch_ha_state`, `_hourly_from_forecast`, `expand_hourly_to_time_step`, `write_price_csvs`
- Produces:
  - `PriceSnapshot` dataclass with `state: dict[str, Any]`, `prices: dict[str, float]`, `last_updated: str | None`
  - `load_price_snapshot(secrets_path: Path, share_dir: Path, *, price_sensor: str, tz_name: str, credentials: tuple[str, str] | None = None) -> PriceSnapshot`
  - `prices_from_ha_state(state: dict[str, Any], share_dir: Path, *, tz_name: str) -> dict[str, float]`
  - `refresh_price_csvs_from_ha(..., snapshot: PriceSnapshot | None = None)`
  - `price_fetch_summary(...)` uses one snapshot (no second HA GET)

- [ ] **Step 1: Write the failing snapshot-count assertion**

In `emhass-cron/tests/test_fetch_prices_emhass.py`, change `test_success_path_reuses_one_snapshot_through_success_finalization` so the price sensor is requested **once** (today it is requested three times). Replace the expected `request_paths` price entries:

```python
        self.assertEqual(
            request_paths,
            [
                "/api/states/sensor.test_soc",
                "/api/states/sensor.test_soc_min",
                "/api/states/sensor.test_soc_max",
                "/api/states/sensor.test_price",
                "/api/history/period/2026-07-27T12%3A00%3A00%2B00%3A00",
                "/api/services/input_datetime/set_datetime",
            ],
        )
```

Also shrink the `urlopen` `side_effect` list: keep one `price_response` instead of three.

Add a unit test that `price_fetch_summary` plus CSV write from the returned snapshot does not call `fetch_ha_state` again:

```python
    def test_price_fetch_summary_does_not_refetch_for_csv_write(self):
        # Arrange a snapshot; patch fetch_ha_state to count calls.
        # Call price_fetch_summary then refresh_price_csvs_from_ha(..., snapshot=...).
        # Assert fetch_ha_state.call_count == 1 for summary-only, and 0 extra on CSV write.
```

- [ ] **Step 2: Run the existing success-path test (expect FAIL)**

Run:

```bash
rtk .venv/bin/python -m unittest emhass-cron.tests.test_fetch_prices_emhass.MpcLoadHistoryFailureTests.test_success_path_reuses_one_snapshot_through_success_finalization -v
```

Expected: FAIL because three `/api/states/sensor.test_price` paths remain.

- [ ] **Step 3: Implement snapshot reuse**

Add:

```python
@dataclass(frozen=True)
class PriceSnapshot:
    state: dict[str, Any]
    prices: dict[str, float]
    last_updated: str | None
```

Move the body of `fetch_prices_from_ha` after `fetch_ha_state` into `prices_from_ha_state`. `load_price_snapshot` fetches once. `fetch_prices_from_ha` becomes `return load_price_snapshot(...).prices` (keep for write-only callers).

Change `price_fetch_summary` to call `load_price_snapshot` once (delete the inner `fetch_prices_from_ha` call).

Change `refresh_price_csvs_from_ha` to accept `snapshot: PriceSnapshot | None = None` and skip HA if provided.

In `main()` MPC live path: call `load_price_snapshot` once; build summary fields from it; pass the same object into `refresh_price_csvs_from_ha`. Keep dry-run using summary only (no CSV write). Keep abort-on-price-failure unchanged.

- [ ] **Step 4: Re-run snapshot tests**

```bash
rtk .venv/bin/python -m unittest discover -s emhass-cron/tests -p 'test_fetch_prices_emhass.py' -v
```

Expected: PASS (including existing credential-snapshot test).

- [ ] **Step 5: Commit (only if authorized)**

```bash
git add emhass-cron/fetch_prices_emhass.py emhass-cron/tests/test_fetch_prices_emhass.py
git commit -m "$(cat <<'EOF'
fix(cron): reuse one Zonneplan price snapshot per MPC

EOF
)"
```

---

### Task 2: Covered-slot `cost_profit` grouping and opt-res reader

**Files:**
- Create: `emhass-cron/planned_profit.py`
- Create: `emhass-cron/tests/test_planned_profit.py`
- Modify: `emhass-cron/Dockerfile` (`COPY emhass-cron/planned_profit.py ./`)

**Interfaces:**
- Consumes: Task 1 `PriceSnapshot.state["attributes"]["forecast"]`; `parse_forecast_datetime` import from `fetch_prices_emhass` **or** duplicate a tiny local datetime parse in `planned_profit.py` to avoid circular imports (prefer local parse + `ZoneInfo`)
- Produces:
  - `LOCAL_TZ = ZoneInfo("Europe/Amsterdam")`
  - `target_date(now: datetime) -> date` → local date + one calendar day
  - `forecast_has_target_date(forecast: list[Any], *, now: datetime) -> bool`
  - `OptResRow(timestamp: datetime, cost_profit: float, optim_status: str | None)`
  - `parse_opt_res_payload(payload: dict[str, Any]) -> list[OptResRow]`
  - `covered_profit(rows: list[OptResRow], plan_date: date, *, step_min: int = 30) -> dict[str, Any] | None`
  - `OPT_RES_READER_PY: str` (stdlib script printed as JSON)
  - `read_opt_res_via_docker(*, container: str, path: str = "/data/opt_res_latest.csv", runner: Callable[..., CompletedProcess[str]] | None = None) -> dict[str, Any]`

`covered_profit` return dict (None if no finite rows for `plan_date`):

```python
{
    "plan_date": "2026-08-17",
    "planned_eur": 3.45,  # round(sum, 2)
    "slot_count": 12,
    "horizon_slots": 32,  # len(rows)
    "coverage_start": "2026-08-17T00:00:00+02:00",
    "coverage_end_exclusive": "2026-08-17T06:00:00+02:00",
    "partial": True,
}
```

`coverage_end_exclusive` = last matching timestamp + `step_min` minutes.

- [ ] **Step 1: Write failing tests in `emhass-cron/tests/test_planned_profit.py`**

```python
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from planned_profit import (  # load via importlib like other cron tests if needed
    covered_profit,
    forecast_has_target_date,
    parse_opt_res_payload,
    target_date,
    OptResRow,
)

CEST = ZoneInfo("Europe/Amsterdam")

class TargetDateTests(unittest.TestCase):
    def test_target_is_next_local_calendar_day(self):
        now = datetime(2026, 8, 16, 16, 30, tzinfo=CEST)
        self.assertEqual(str(target_date(now)), "2026-08-17")

    def test_forecast_detects_tomorrow_slot(self):
        now = datetime(2026, 8, 16, 16, 30, tzinfo=CEST)
        forecast = [
            {"datetime": "2026-08-16T14:00:00+00:00", "electricity_price": 1},
            {"datetime": "2026-08-17T00:00:00+00:00", "electricity_price": 1},
        ]
        self.assertTrue(forecast_has_target_date(forecast, now=now))
        self.assertFalse(
            forecast_has_target_date(forecast[:1], now=now)
        )

class CoveredProfitTests(unittest.TestCase):
    def test_sums_only_target_date_finite_cost_profit(self):
        rows = [
            OptResRow(datetime(2026, 8, 16, 23, 30, tzinfo=CEST), 1.0, "Optimal"),
            OptResRow(datetime(2026, 8, 17, 0, 0, tzinfo=CEST), 1.111, "Optimal"),
            OptResRow(datetime(2026, 8, 17, 0, 30, tzinfo=CEST), -0.211, "Optimal"),
            OptResRow(datetime(2026, 8, 17, 1, 0, tzinfo=CEST), float("nan"), "Optimal"),
        ]
        got = covered_profit(rows, datetime(2026, 8, 17, tzinfo=CEST).date())
        self.assertEqual(got["planned_eur"], 0.90)
        self.assertEqual(got["slot_count"], 2)
        self.assertEqual(got["horizon_slots"], 4)
        self.assertEqual(got["coverage_start"], "2026-08-17T00:00:00+02:00")
        self.assertEqual(got["coverage_end_exclusive"], "2026-08-17T01:00:00+02:00")
        self.assertTrue(got["partial"])

    def test_none_when_no_matching_rows(self):
        rows = [OptResRow(datetime(2026, 8, 16, 15, 0, tzinfo=CEST), 1.0, "Optimal")]
        self.assertIsNone(covered_profit(rows, datetime(2026, 8, 17, tzinfo=CEST).date()))

    def test_naive_timestamp_treated_as_utc(self):
        naive = datetime(2026, 8, 16, 22, 0)  # 00:00 CEST next day
        rows = [OptResRow(naive, 2.0, "Optimal")]
        got = covered_profit(rows, datetime(2026, 8, 17, tzinfo=CEST).date())
        self.assertEqual(got["slot_count"], 1)

    def test_dst_spring_forward_local_dates(self):
        # 2026-03-29 02:00 CEST skipped; 01:30 CET and 03:00 CEST are distinct local dates.
        rows = [
            OptResRow(datetime(2026, 3, 29, 1, 30, tzinfo=CEST), 0.1, "Optimal"),
            OptResRow(datetime(2026, 3, 29, 3, 0, tzinfo=CEST), 0.2, "Optimal"),
        ]
        got = covered_profit(rows, datetime(2026, 3, 29, tzinfo=CEST).date())
        self.assertEqual(got["slot_count"], 2)
        self.assertEqual(got["planned_eur"], 0.3)

class ParseOptResTests(unittest.TestCase):
    def test_parse_payload_rejects_missing_cost_profit(self):
        with self.assertRaises(ValueError):
            parse_opt_res_payload({"rows": [{"timestamp": "2026-08-17T00:00:00+02:00"}]})

    def test_parse_payload_skips_non_finite_then_groups(self):
        payload = {
            "optim_status": "Optimal",
            "rows": [
                {"timestamp": "2026-08-17T00:00:00+02:00", "cost_profit": 1.0, "optim_status": "Optimal"},
            ],
        }
        rows = parse_opt_res_payload(payload)
        self.assertEqual(len(rows), 1)
```

Load `planned_profit` with `importlib` from `emhass-cron/planned_profit.py` the same way cron tests load `fetch_prices_emhass.py`.

- [ ] **Step 2: Run tests (expect FAIL — module missing)**

```bash
rtk .venv/bin/python -m unittest discover -s emhass-cron/tests -p 'test_planned_profit.py' -v
```

Expected: FAIL (`ModuleNotFoundError` or missing functions).

- [ ] **Step 3: Implement `planned_profit.py` grouping + reader**

Rules:

- Convert timestamps to `Europe/Amsterdam` before `.date()`.
- Naive datetimes: attach UTC, then convert.
- Ignore non-finite `cost_profit` (`math.isfinite`).
- `parse_opt_res_payload` requires a `rows` list; each row needs parseable `timestamp` and numeric `cost_profit` (skip bad rows; if zero valid rows remain, raise `ValueError` only when the payload itself is malformed, not when grouping finds zero target-date rows).
- `OPT_RES_READER_PY` uses `csv` + `json` only. Read `/data/opt_res_latest.csv`. Emit `{"optim_status": ..., "row_count": N, "rows": [{"timestamp": iso, "cost_profit": float, "optim_status": str|null}]}`.
- `read_opt_res_via_docker` runs `docker exec $container python3 -c OPT_RES_READER_PY`. Inject `runner` in tests (do not require Docker). Non-zero exit or invalid JSON → raise `RuntimeError` (caller treats as auxiliary failure).

Do **not** persist or talk to HA in this task.

Dockerfile:

```dockerfile
COPY emhass-cron/fetch_prices_emhass.py emhass-cron/planned_profit.py emhass-cron/crontab emhass-cron/entrypoint.sh ./
```

- [ ] **Step 4: Run tests (expect PASS)**

```bash
rtk .venv/bin/python -m unittest discover -s emhass-cron/tests -p 'test_planned_profit.py' -v
```

- [ ] **Step 5: Commit (only if authorized)**

```bash
git add emhass-cron/planned_profit.py emhass-cron/tests/test_planned_profit.py emhass-cron/Dockerfile
git commit -m "$(cat <<'EOF'
feat(cron): group EMHASS cost_profit by local date

EOF
)"
```

---

### Task 3: Write-once JSON lock and compact helper payload

**Files:**
- Modify: `emhass-cron/planned_profit.py`
- Modify: `emhass-cron/tests/test_planned_profit.py`

**Interfaces:**
- Consumes: Task 2 `covered_profit` dict
- Produces:
  - `JSON_NAME = "daily_planned_profit.json"`
  - `LOCK_NAME = "daily_planned_profit.json.lock"`
  - `HELPER_ENTITY_ID = "input_text.emhass_planned_profit_payload"`
  - `MAX_HELPER_CHARS = 255`
  - `load_store(share_dir: Path) -> dict[str, Any]`
  - `persist_lock(share_dir: Path, record: dict[str, Any], *, now: datetime) -> str`  
    returns `"written"` | `"exists"`  
    `record` must include `plan_date` plus fields from `covered_profit` plus `locked_at`, `mpc_completed_at`, `locked_on_date`
  - `day_record(share_dir: Path, plan_date: date) -> dict[str, Any] | None`
  - `helper_payload(record: dict[str, Any]) -> str`  
    compact JSON keys only: `plan_date`, `planned_eur`, `locked_at`, `slot_count`, `coverage_start`, `coverage_end_exclusive`, `partial`  
    raise `ValueError` if `len(payload) > 255`

Store envelope:

```json
{
  "version": 1,
  "timezone": "Europe/Amsterdam",
  "metric": "emhass_cost_profit_covered_slots",
  "days": {}
}
```

- [ ] **Step 1: Write failing persist/payload tests**

```python
class PersistLockTests(unittest.TestCase):
    def test_write_once_does_not_overwrite(self):
        # persist_lock twice with different planned_eur; second returns "exists";
        # file still has first value.

    def test_prune_removes_only_keys_older_than_14_local_days(self):
        # Insert 2026-07-01 and 2026-08-16; persist 2026-08-17 with now=2026-08-16;
        # 2026-07-01 gone; 2026-08-16 remains; never remove a future key.

    def test_malformed_store_starts_fresh_without_raising(self):
        # Write garbage JSON; persist_lock still writes a valid envelope.

    def test_helper_payload_stays_under_255(self):
        payload = helper_payload({...full example from spec...})
        self.assertLessEqual(len(payload), 255)
        self.assertNotIn(" ", payload)  # compact separators
```

For overlapping writers: hold `fcntl.LOCK_EX` on the lock file in a thread, start `persist_lock` in another thread, release, then assert only one record and no exception. If that is flaky on the runner, a sequential test that the lock file is created is the minimum; prefer a real overlap test.

- [ ] **Step 2: Run tests (expect FAIL)**

```bash
rtk .venv/bin/python -m unittest emhass-cron.tests.test_planned_profit.PersistLockTests -v
```

- [ ] **Step 3: Implement persist + payload**

- `fcntl.flock` exclusive around read → prune → write.
- Atomic replace: write `daily_planned_profit.json.tmp` then `Path.replace`.
- Never overwrite `days[plan_date]`.
- On write, drop keys whose date `< now.date() - 14 days`. Keep future keys.
- `load_store`: missing file → empty envelope; malformed JSON → empty envelope (do not crash lock path).
- `helper_payload`: `json.dumps(obj, separators=(",", ":"), sort_keys=True)`.

- [ ] **Step 4: Run tests (expect PASS)**

```bash
rtk .venv/bin/python -m unittest discover -s emhass-cron/tests -p 'test_planned_profit.py' -v
```

- [ ] **Step 5: Commit (only if authorized)**

```bash
git add emhass-cron/planned_profit.py emhass-cron/tests/test_planned_profit.py
git commit -m "$(cat <<'EOF'
feat(cron): persist write-once planned-profit locks

EOF
)"
```

---

### Task 4: Auxiliary lock after successful MPC + helper publish

**Files:**
- Modify: `emhass-cron/planned_profit.py`
- Modify: `emhass-cron/fetch_prices_emhass.py`
- Modify: `emhass-cron/tests/test_fetch_prices_emhass.py`
- Modify: `emhass-cron/tests/test_planned_profit.py`

**Interfaces:**
- Consumes: Task 1 `PriceSnapshot`; Task 2 reader/grouping; Task 3 persist/payload; existing `finalize_successful_mpc_run`, `write_run_status`, credential snapshot
- Produces:
  - `publish_ha_input_text(secrets_path, entity_id, value, *, credentials=None) -> None`  
    POST `{hass_url}api/services/input_text/set_value` with `{"entity_id", "value"}` (same retry style as `publish_ha_input_datetime`)
  - `run_planned_profit_side_effect(*, share_dir, snapshot, completed_at, secrets_path, credentials, docker_runner=None) -> None`  
    never raises to caller after logging; writes `planned_profit` run-status
  - Call `run_planned_profit_side_effect` **only if** `finalize_successful_mpc_run` returned `True`

Algorithm for `run_planned_profit_side_effect`:

1. `plan_date = target_date(completed_at)`
2. If `forecast_has_target_date(snapshot.state["attributes"]["forecast"], now=completed_at)` and `day_record(share_dir, plan_date) is None`:
   - `payload = read_opt_res_via_docker(...)` (catch → `write_run_status(..., "planned_profit", ok=False, error=...)`; return)
   - If `optim_status` present and not `Optimal` → no lock, `ok=True` with `locked=false` optional; do not fail MPC
   - `rows = parse_opt_res_payload(payload)`; `covered = covered_profit(rows, plan_date)`
   - If `covered is None` → no lock
   - Else build record (`locked_at`/`mpc_completed_at` = `completed_at.isoformat()`, `locked_on_date` = local date of `completed_at`) and `persist_lock`
3. Always after that (including when no new lock): `today = completed_at.astimezone(LOCAL_TZ).date()`; `rec = day_record(share_dir, today)`; if `rec` is not None, `publish_ha_input_text(..., helper_payload(rec))`. If no today record, **do not** write the helper.
4. Helper/publish/lock I/O errors → `write_run_status(share_dir, "planned_profit", ok=False, error=...)`. Do not raise. Do not call `write_run_status(..., "mpc", ok=False)`.

- [ ] **Step 1: Write failing tests**

Helper publish:

```python
    def test_helper_payload_posts_input_text_set_value(self, mock_urlopen):
        # publish_ha_input_text(..., "input_text.emhass_planned_profit_payload", compact)
        # assert path endswith /api/services/input_text/set_value
```

Main flow (extend MPC success test or new test with patches):

```python
    def test_planned_profit_lock_runs_only_after_successful_finalization(self):
        # Patch finalize_successful_mpc_run to return False;
        # patch run_planned_profit_side_effect; assert it is not called.

    def test_planned_profit_aux_error_does_not_clear_mpc_ok(self):
        # Successful finalize; run_planned_profit_side_effect's docker reader raises;
        # exit 0; last_enever_run mpc.ok True; planned_profit.ok False.

    def test_no_lock_without_tomorrow_slots(self):
        # Snapshot forecast only today; after success, JSON has no tomorrow key.

    def test_lock_uses_snapshot_forecast_not_a_second_ha_get(self):
        # After Task 1 path, still exactly one price GET; lock still created when
        # forecast includes tomorrow and docker runner returns Optimal rows.
```

Wire `run_planned_profit_side_effect` with an injectable `docker_runner` returning a JSON payload of 12 tomorrow rows so tests do not need Docker.

- [ ] **Step 2: Run tests (expect FAIL)**

```bash
rtk .venv/bin/python -m unittest discover -s emhass-cron/tests -p 'test_*.py' -v
```

- [ ] **Step 3: Implement publish + side effect + `main()` hook**

Insert after `finalized = finalize_successful_mpc_run(...)`:

```python
        if finalized:
            run_planned_profit_side_effect(
                share_dir=args.share_dir,
                snapshot=price_snapshot,
                completed_at=completed_at,
                secrets_path=args.secrets,
                credentials=load_history_credentials,
            )
        return 0 if finalized else 1
```

Keep `price_snapshot` in scope from Task 1. Dry-run / write-only / soc_refresh / failed chain must not call the side effect.

- [ ] **Step 4: Run all cron tests (expect PASS)**

```bash
rtk .venv/bin/python -m unittest discover -s emhass-cron/tests -p 'test_*.py' -v
```

- [ ] **Step 5: Commit (only if authorized)**

```bash
git add emhass-cron/planned_profit.py emhass-cron/fetch_prices_emhass.py emhass-cron/tests
git commit -m "$(cat <<'EOF'
feat(cron): lock tomorrow covered-slot profit after MPC

EOF
)"
```

---

### Task 5: Missed-lock monitor alert (22:00, once per target)

**Files:**
- Modify: `monitor/health_notify.py`
- Modify: `monitor/tests/test_health_notify.py`

**Interfaces:**
- Consumes: `/share/daily_planned_profit.json`; existing `notify_ha`, `load_alert_state`, `persist_alert_state`, `AlertState`
- Produces:
  - `PLANNED_PROFIT_STATE_KEY = "planned_profit_missing"`
  - `planned_profit_alert_signature(target: date) -> str` → `planned_profit_missing:YYYY-MM-DD`
  - `local_deadline_reached(now: datetime) -> bool` → local hour:minute >= 22:00 in `TZ`
  - `lock_state_for_target(share: Path, target: date) -> str` → `"locked"` | `"missing"` | `"invalid"`
  - `process_planned_profit_deadline(*, share, state, now, base_url, token, service) -> int`  
    sent count 0 or 1; does **not** use `record_recoverable_incident` / 60-minute escalation

Behavior:

- If not `local_deadline_reached(now)`: if lock exists for `target_date = local_today+1`, clear `state[PLANNED_PROFIT_STATE_KEY]` signature for that target (so a late lock before 22:00 never notifies). Return 0.
- If deadline reached and state is `"locked"`: clear signature; no notify; no recovery message.
- If `"missing"` or `"invalid"`: signature = `planned_profit_missing:<target>`; if `entry.notification_signature == signature` and `last_notify` set → skip; else `notify_ha` with title `Energy: Planned profit` and message:
  - missing: `No EMHASS planned-profit lock exists for tomorrow (YYYY-MM-DD). A day-ahead price forecast or a successful MPC may be missing.`
  - invalid: `EMHASS planned-profit lock record is invalid; tomorrow (YYYY-MM-DD) is treated as unlocked.`
- Notify failure: do not set `last_notify` / signature; return 0 so the next 10-minute poll retries.
- Missing JSON file → `"missing"`. Malformed JSON → `"invalid"`.

Call `process_planned_profit_deadline` from `main()` after existing failure handling and **before** `persist_alert_state`.

Persist: set `AlertState.notification_signature` and `last_notify` so `persist_alert_state` writes an object (not a bare float).

- [ ] **Step 1: Write failing tests**

```python
class PlannedProfitDeadlineTests(unittest.TestCase):
    def test_before_2200_does_not_notify(self): ...
    def test_2200_missing_sends_once(self): ...
    def test_same_target_does_not_repeat_after_success(self): ...
    def test_delivery_failure_retries(self): ...
    def test_late_lock_clears_pending_without_recovery_notify(self): ...
    def test_malformed_json_uses_invalid_message_once(self): ...
    def test_dst_2200_uses_amsterdam(self):
        # 2026-10-25 22:00 Europe/Amsterdam is after deadline.
```

Patch `notify_ha`. Use temp `share` with/without JSON.

- [ ] **Step 2: Run tests (expect FAIL)**

```bash
rtk .venv/bin/python -m unittest monitor.tests.test_health_notify.PlannedProfitDeadlineTests -v
```

- [ ] **Step 3: Implement deadline path**

Do not add this id to `HARD_ALERT_IDS` or recoverable B2 lists.

- [ ] **Step 4: Run monitor tests (expect PASS)**

```bash
rtk .venv/bin/python -m unittest discover -s monitor/tests -p 'test_health_notify.py' -v
```

- [ ] **Step 5: Commit (only if authorized)**

```bash
git add monitor/health_notify.py monitor/tests/test_health_notify.py
git commit -m "$(cat <<'EOF'
feat(monitor): alert once if tomorrow profit lock missing

EOF
)"
```

---

### Task 6: Home Assistant helper, sensor, and dashboard

**Files:**
- Modify: `home-assistant/ha_emhass_helpers.yaml`
- Modify: `home-assistant/ha_emhass_dashboard.yaml`
- Modify: `home-assistant/dashboards/emhass_trading.yaml`
- Modify: `scripts/tests/test_sigen_ems_package.py`
- Modify: `scripts/tests/test_emhass_trading_dashboard.py`
- Optional: `scripts/tests/test_ha_emhass_helpers.py` if helper assertions do not fit existing files

**Interfaces:**
- Consumes: helper JSON from Task 3/4
- Produces: HA entities named in the spec

- [ ] **Step 1: Write failing structural tests**

In `scripts/tests/test_sigen_ems_package.py` (or helpers test):

- `input_text.emhass_planned_profit_payload` exists with `max: 255`
- Sensor `EMHASS planned profit today`:
  - `default_entity_id: sensor.emhass_planned_profit_today`
  - `unit_of_measurement: EUR`, `device_class: monetary`, `state_class: total`
  - state uses `from_json` on `input_text.emhass_planned_profit_payload`
  - returns `unknown` unless `plan_date == now().strftime('%Y-%m-%d')`
  - attributes: `plan_date`, `locked_at`, `slot_count`, `coverage_start`, `coverage_end_exclusive`, `partial`
  - no `POST`/`rest` command ownership
  - no zero fallback

Change `_sensor_block` neighbors: insert planned sensor **before** month or after today as chosen; update `test_month_profit_is_production_minus_delivery` `next_name` if the order changes. Recommended order in `ha_emhass_dashboard.yaml`: today realized → planned today → month.

In `scripts/tests/test_emhass_trading_dashboard.py`:

- Prices entities include `sensor.emhass_planned_profit_today` named `Planned today (covered slots)` immediately after net profit today
- Graph title `Planned vs realized profit — last 7 days`
- Two series: planned then realized, names from spec, `stat_types: [state]`, `days_to_show: 7`, `period: day`, `chart_type: bar`

- [ ] **Step 2: Run tests (expect FAIL)**

```bash
rtk .venv/bin/python -m unittest scripts.tests.test_emhass_trading_dashboard scripts.tests.test_sigen_ems_package -v
```

- [ ] **Step 3: Implement YAML**

`ha_emhass_helpers.yaml` add:

```yaml
input_text:
  emhass_planned_profit_payload:
    name: EMHASS planned profit payload
    max: 255
```

(`input_number` / `input_datetime` already exist; add `input_text` as a sibling root key.)

Template sensor (Jinja must match tests):

```yaml
      - name: EMHASS planned profit today
        unique_id: emhass_planned_profit_today
        default_entity_id: sensor.emhass_planned_profit_today
        unit_of_measurement: EUR
        device_class: monetary
        state_class: total
        icon: mdi:cash-clock
        state: >
          {% set raw = states('input_text.emhass_planned_profit_payload') %}
          {% if raw in ['unknown', 'unavailable', 'none', ''] %}
            unknown
          {% else %}
            {% set p = raw | from_json %}
            {% if p.plan_date is defined and p.plan_date == now().strftime('%Y-%m-%d') and p.planned_eur is defined %}
              {{ p.planned_eur }}
            {% else %}
              unknown
            {% endif %}
          {% endif %}
        attributes:
          plan_date: >
            {% set raw = states('input_text.emhass_planned_profit_payload') %}
            {% if raw not in ['unknown', 'unavailable', 'none', ''] %}
              {{ (raw | from_json).plan_date | default(none) }}
            {% endif %}
          locked_at: >
            {% set raw = states('input_text.emhass_planned_profit_payload') %}
            {% if raw not in ['unknown', 'unavailable', 'none', ''] %}
              {{ (raw | from_json).locked_at | default(none) }}
            {% endif %}
          slot_count: >
            {% set raw = states('input_text.emhass_planned_profit_payload') %}
            {% if raw not in ['unknown', 'unavailable', 'none', ''] %}
              {{ (raw | from_json).slot_count | default(none) }}
            {% endif %}
          coverage_start: >
            {% set raw = states('input_text.emhass_planned_profit_payload') %}
            {% if raw not in ['unknown', 'unavailable', 'none', ''] %}
              {{ (raw | from_json).coverage_start | default(none) }}
            {% endif %}
          coverage_end_exclusive: >
            {% set raw = states('input_text.emhass_planned_profit_payload') %}
            {% if raw not in ['unknown', 'unavailable', 'none', ''] %}
              {{ (raw | from_json).coverage_end_exclusive | default(none) }}
            {% endif %}
          partial: >
            {% set raw = states('input_text.emhass_planned_profit_payload') %}
            {% if raw not in ['unknown', 'unavailable', 'none', ''] %}
              {{ (raw | from_json).partial | default(none) }}
            {% endif %}
```

Dashboard Prices + graph exactly as spec YAML (replace current single-series 7-day graph).

Do not change realized profit templates.

- [ ] **Step 4: Run structural tests + render**

```bash
rtk .venv/bin/python -m unittest scripts.tests.test_emhass_trading_dashboard scripts.tests.test_sigen_ems_package -v
rtk ./scripts/render-ha-packages.sh
```

Expected: PASS; render writes `.rendered/` without error.

- [ ] **Step 5: Commit (only if authorized)**

```bash
git add home-assistant/ha_emhass_helpers.yaml home-assistant/ha_emhass_dashboard.yaml home-assistant/dashboards/emhass_trading.yaml scripts/tests/test_sigen_ems_package.py scripts/tests/test_emhass_trading_dashboard.py
git commit -m "$(cat <<'EOF'
feat(ha): show covered-slot planned profit vs realized

EOF
)"
```

---

### Task 7: Ignore live files, docs, and verification sweep

**Files:**
- Modify: `.gitignore`
- Modify: `emhass-cron/README.md`
- Modify: `docs/ENTITY_CONTRACT.md`
- Modify: `memory-bank/activeContext.md`
- Modify: `memory-bank/techContext.md`

- [ ] **Step 1: Add gitignore entries**

```gitignore
emhass/share/daily_planned_profit.json
emhass/share/daily_planned_profit.json.lock
emhass/share/daily_planned_profit.json.tmp
```

- [ ] **Step 2: Document in `emhass-cron/README.md`**

Add a section **Planned profit lock**:

- Runs only after successful MPC marker
- Source: same price snapshot + `docker exec emhass python3` on `/data/opt_res_latest.csv`
- File: `/share/daily_planned_profit.json`
- HA: `input_text.set_value` → `input_text.emhass_planned_profit_payload`
- Metric: covered-slot `cost_profit` for tomorrow; `partial: true`
- Auxiliary failure does not fail MPC

Update ENTITY_CONTRACT required-entities table with the helper and sensor (energy-stack owned). Date the doc **2026-08-17**, author **Composer**.

Memory bank: short note that the feature is in repo, not live until deploy; link spec + this plan.

- [ ] **Step 3: Full verification commands from the spec**

```bash
rtk .venv/bin/python -m unittest discover -s emhass-cron/tests -p 'test_*.py' -v
rtk .venv/bin/python -m unittest discover -s monitor/tests -p 'test_*.py' -v
rtk .venv/bin/python -m unittest scripts.tests.test_emhass_trading_dashboard -v
rtk ./scripts/render-ha-packages.sh
```

All must PASS before claiming the task complete. Live deploy is **out of this plan** until the operator runs:

```bash
source scripts/lib/source-site-env.sh
./scripts/deploy-energy-stack.sh
./scripts/deploy-home-assistant.sh
```

Live checks stay as in the spec (first tomorrow-key lock, no overwrite, sensor on target date, 22:00 notify, graph series).

- [ ] **Step 4: Commit (only if authorized)**

```bash
git add .gitignore emhass-cron/README.md docs/ENTITY_CONTRACT.md memory-bank/activeContext.md memory-bank/techContext.md
git commit -m "$(cat <<'EOF'
docs: planned-profit lock contract and gitignore

EOF
)"
```

---

## Spec coverage (self-review)

| Spec requirement | Task |
|------------------|------|
| One price snapshot for CSV + tomorrow detect | 1, 4 |
| Lock only after successful MPC marker | 4 |
| `docker exec` stdlib opt-res read; no pandas in cron | 2, 4 |
| Sum `cost_profit` for tomorrow local date; `partial` | 2 |
| Write-once JSON, atomic, flock, prune 14d | 3 |
| Helper ≤255; `input_text.set_value`; no sensor POST | 3, 4, 6 |
| Template sensor date-gates; unknown not zero | 6 |
| Dashboard names + dual 7-day `state` bars | 6 |
| Monitor 22:00 once-per-target; retry; late lock clear | 5 |
| Auxiliary error does not set `mpc.ok=false` | 4 |
| gitignore live JSON; README; no compose change | 7 (Dockerfile COPY only in 2) |
| No full-day merge / horizon change / Telegram | Global constraints |

No remaining spec item without a task.
