# EMHASS daily planned profit (tomorrow lock)

**Date:** 2026-08-16
**Author:** Composer
**Status:** Reviewed and revised before implementation

## Review outcome

The original design had a sound lock idea, but it was not ready for
implementation. The review found four important gaps:

1. The live MPC has a maximum horizon of 32 half-hour slots (16 hours). The
   first afternoon MPC cannot produce a complete 24-hour plan for tomorrow.
2. `opt_res_latest.csv` is in the `emhass` container at
   `/data/opt_res_latest.csv`. It is not in the shared `/share` directory.
3. The lock must use the same price forecast that created the successful CSV
   files. A second Home Assistant forecast request can return different data.
4. A template sensor cannot also be safely owned by direct Home Assistant REST
   state writes. The design needs a durable Home Assistant helper as the
   transport path.

This revision changes the metric name and the interface contracts. It keeps
the live MPC limit of 32 slots. It does not claim that the locked value is a
full-day forecast.

## Goal

Show the following on the EMHASS trading dashboard:

- the locked EMHASS planned profit for the part of tomorrow covered by the
  first successful MPC that contains tomorrow price slots;
- the existing realized profit for today; and
- a seven-day chart with planned covered-slot profit and realized profit.

The planned and realized values are not the same measurement window when the
MPC covers only part of tomorrow. The dashboard must show this in the series
name and in the sensor attributes.

A complete 24-hour planned profit is not part of this design. It needs a
separate planning method that does not change the live 32-slot control
horizon.

## Decisions

| Topic | Decision |
|---|---|
| Approach | Lock data in `emhass-cron` after a successful MPC |
| Lock target | Tomorrow's local calendar date |
| Lock trigger | First successful MPC whose exact fetched price snapshot and validated result both contain at least one tomorrow slot |
| Metric | Sum EMHASS `cost_profit` for result rows dated tomorrow in `Europe/Amsterdam` |
| Metric meaning | Planned profit for covered MPC slots; not a full calendar-day estimate |
| Lock write | Atomic, write-once date key; never overwrite a date key |
| Source of truth | `/share/daily_planned_profit.json` |
| HA transport | Compact JSON in `input_text.emhass_planned_profit_payload` |
| HA sensor | `sensor.emhass_planned_profit_today`, derived from the helper |
| Missed lock | One immediate `notify.notify` per missing target date, with a durable signature |
| Notification channel | Home Assistant `notify.notify` only; no Telegram |
| UI | Planned covered-slot profit today, realized profit today, and a seven-day dual bar chart |

Rejected alternatives remain rejected:

- next-24-hour locking;
- locking only the remaining part of today;
- using live `sensor.total_cost_fun_value`;
- using an unbounded EMHASS horizon for the live MPC; and
- building a full-day value by merging different MPC runs.

## Current stack constraints

The implementation must keep these existing contracts:

- `optimization_time_step` is 30 minutes;
- `prediction_horizon` is at most 32;
- price methods are `csv`;
- weather method is `open-meteo`;
- a price fetch failure aborts the MPC; and
- the existing successful-MPC marker is written only after optimization and
  `publish-data` succeed.

The current Compose setup already gives both `emhass-cron` and
`energy-monitor` read-only access to the Docker socket. It also sets
`EMHASS_CONTAINER=emhass`. No new Docker mount is required.

EMHASS writes the optimization result to `/data/opt_res_latest.csv` inside the
`emhass` container. The cron container must read it through
`docker exec "$EMHASS_CONTAINER"`; it must not assume that
`/share/opt_res_latest.csv` exists.

The current EMHASS result code creates a `cost_profit` column. It calculates
the per-slot currency value from grid import and export, including the
30-minute duration. The implementation must use this column as supplied. It
must not recalculate profit from `P_grid` or use `total_cost_fun_value`.

Reference: [EMHASS result cost calculation](https://github.com/davidusb-geek/emhass/blob/master/src/emhass/optimization.py#L3834-L3849).

## Metric

For a target local date `D`:

```text
planned_covered_profit(D) = round(sum(row.cost_profit for rows where local_date(row.timestamp) == D), 2)
```

Rules:

- Convert the result index to `Europe/Amsterdam` before grouping by date.
- Accept only finite numeric `cost_profit` values.
- Require the result timestamp to be parseable and timezone-aware, or to be
  explicitly interpreted as UTC by the EMHASS reader contract.
- Store the number of matching rows as `slot_count`.
- Store `coverage_start` and `coverage_end_exclusive` in local ISO-8601 form.
- `coverage_end_exclusive` is the last matching timestamp plus the configured
  30-minute step.
- A result with no tomorrow row does not create a lock.
- A missing, non-numeric, or malformed `cost_profit` column does not create a
  lock. It also does not change a successful MPC into a failed MPC.

The value can be negative. EMHASS already applies the profit sign and the
time-step conversion. Round only the stored and published value to two
decimal places.

Example: the first afternoon MPC may contain only 12 tomorrow rows. The
stored value is then the planned profit for those 12 rows, with
`slot_count: 12` and `partial: true`.

## Lock pipeline

### 1. Capture one price snapshot

The normal `mpc` path must fetch the Zonneplan HA state once for the run. The
same parsed snapshot must be used to:

- build the import and export price dictionaries;
- write the two CSV files;
- determine whether tomorrow slots exist; and
- record the source `last_updated` value for diagnostics.

The current normal path calls `price_fetch_summary()` and then fetches the HA
price state again while writing CSV files. Refactor this path so the lock
decision uses the price snapshot that wrote the CSV files. Do not make a
second forecast request for the lock decision.

Use local date arithmetic:

```text
target_date = run_now_in_Europe/Amsterdam.date() + one calendar day
```

A slot is a tomorrow slot when its parsed local date equals `target_date`.
Do not use a fixed clock such as 14:00. The current site data shows that the
Zonneplan forecast can appear earlier or later.

### 2. Run the existing MPC chain

Keep the current order:

```text
price CSVs -> load-history validation -> weather cache -> naive-mpc-optim -> publish-data
```

Do not lock after a failed MPC, dry run, write-only run, publish-only run,
SOC-refresh run, or weather-only run.

### 3. Confirm the existing successful-MPC marker

Keep the current finalization contract. The Home Assistant
`input_datetime.emhass_last_successful_mpc` must be updated successfully
before the planned-profit lock is considered eligible.

The planned-profit side effect is auxiliary. If its file write, result read,
or HA helper publish fails, the already successful MPC must remain successful.
Record the auxiliary failure in a separate `planned_profit` block in
`last_enever_run.json`. The next successful MPC retries the lock and the
helper publish.

### 4. Read and validate the result

After successful MPC finalization, read the result with the existing Docker
boundary. The preferred contract is:

```text
docker exec "$EMHASS_CONTAINER" python3 <small stdlib reader> /data/opt_res_latest.csv
```

The reader must return only the required fields as JSON:

- timestamp;
- `cost_profit`; and
- optionally the result row count and EMHASS status.

Validate that the result has status `Optimal`, contains `cost_profit`, and has
at least one finite row for `target_date`. Do not copy the complete result to
the host or add pandas to `emhass-cron`.

### 5. Persist the first lock

If `daily_planned_profit.json` has no `target_date` key, calculate and write
the lock. If the key exists, do not recalculate or overwrite it.

Use an in-process file lock around the read, prune, and write sequence. Write
the new JSON to a temporary file in the same directory and replace the target
file atomically. This protects the write-once rule if two cron jobs overlap.

### 6. Publish the current-day record to Home Assistant

After each successful MPC, load the record for the current local date from
the JSON source of truth. If it exists, publish its compact payload to
`input_text.emhass_planned_profit_payload`. If it does not exist, do not
replace a valid helper value with a fake zero.

The helper publish is idempotent. It can run even when no new lock was made.
This makes the midnight sensor change depend on the restored helper and the
current date, not only on a narrow cron timing window.

## Persistence contract

File on the energy host:

```text
emhass/share/daily_planned_profit.json
```

The same file is `/share/daily_planned_profit.json` in `emhass-cron` and
`energy-monitor`.

Schema:

```json
{
  "version": 1,
  "timezone": "Europe/Amsterdam",
  "metric": "emhass_cost_profit_covered_slots",
  "days": {
    "2026-08-17": {
      "planned_eur": 3.45,
      "plan_date": "2026-08-17",
      "locked_at": "2026-08-16T14:00:52+02:00",
      "mpc_completed_at": "2026-08-16T14:00:52+02:00",
      "locked_on_date": "2026-08-16",
      "slot_count": 12,
      "horizon_slots": 32,
      "coverage_start": "2026-08-17T00:00:00+02:00",
      "coverage_end_exclusive": "2026-08-17T06:00:00+02:00",
      "partial": true
    }
  }
}
```

Rules:

- The key and `plan_date` are the target local date in ISO `YYYY-MM-DD` form.
- `planned_eur` is finite and rounded to two decimal places.
- `slot_count` is greater than zero for a lock.
- `horizon_slots` is the number of rows in the validated result, normally at
  most 32.
- `partial` is true for this design. It prevents readers from treating the
  value as a complete calendar-day estimate.
- Never overwrite an existing date key, even if a later MPC has a different
  value.
- On each successful lock write, remove keys older than 14 local calendar
  days. Never remove future keys.
- Use atomic writes and never log secrets or the HA token.
- Add the JSON file and its lock file to `.gitignore`.

The JSON file is the source of truth. Home Assistant state and recorder data
are derived views and must not be used to rebuild a missing lock.

## Home Assistant contract

### Helper transport

Add this restart-restored helper to `home-assistant/ha_emhass_helpers.yaml`:

```yaml
input_text:
  emhass_planned_profit_payload:
    name: EMHASS planned profit payload
    max: 255
```

The cron calls `input_text.set_value` through the existing HA REST API. Use
compact JSON so the value stays below the 255-character helper limit. The
implementation must test the encoded length before sending it.

Example helper value:

```json
{"plan_date":"2026-08-17","planned_eur":3.45,"locked_at":"2026-08-16T14:00:52+02:00","slot_count":12,"coverage_start":"2026-08-17T00:00:00+02:00","coverage_end_exclusive":"2026-08-17T06:00:00+02:00","partial":true}
```

Do not use direct `POST /api/states/sensor.emhass_planned_profit_today` for
this entity. The entity is a Home Assistant template sensor and must have one
state owner.

### Planned sensor

Add this sensor to `home-assistant/ha_emhass_dashboard.yaml`:

| Field | Value |
|---|---|
| Name | EMHASS planned profit today |
| Entity ID | `sensor.emhass_planned_profit_today` |
| Unit | `EUR` |
| Device class | `monetary` |
| State class | `total` |
| Source | `input_text.emhass_planned_profit_payload` |

The template sensor must:

- parse the helper JSON;
- return `planned_eur` only when `plan_date` equals today's local date;
- return `unknown` for invalid JSON, a missing record, or another date; and
- expose `plan_date`, `locked_at`, `slot_count`, `coverage_start`,
  `coverage_end_exclusive`, and `partial` as attributes. The full
  `mpc_completed_at`, `locked_on_date`, and `horizon_slots` metadata remains
  in the JSON source of truth because it is not needed in the 255-character
  transport payload.

The sensor changes to `unknown` at midnight when no plan for the new date is
available. It does not use zero as a missing value. The helper restores the
last payload after an HA restart, and the date check prevents yesterday's
value from being shown as today's plan.

### Realized sensor

Keep `sensor.emhass_net_electricity_profit_today` unchanged:

```text
realized(day) = production_costs − delivery_costs
```

It remains the Zonneplan inclusive-of-tax value.

## Dashboard

In `home-assistant/dashboards/emhass_trading.yaml`:

1. Add `sensor.emhass_planned_profit_today` to the Prices card with the name
   **Planned today (covered slots)**.
2. Replace the current single-series seven-day graph with:

```yaml
- type: statistics-graph
  title: Planned vs realized profit — last 7 days
  days_to_show: 7
  period: day
  chart_type: bar
  stat_types:
    - state
  entities:
    - entity: sensor.emhass_planned_profit_today
      name: Planned (covered slots, EUR)
    - entity: sensor.emhass_net_electricity_profit_today
      name: Realized (EUR)
```

Use `stat_types: [state]`. The planned sensor has a stable numeric state on
the target day and the recorder keeps the last numeric value for that day.
The planned bar is the locked covered-slot value. The realized bar is the
continuous full-day value for the actual day.

## Missed-lock alert

The owner is `energy-monitor` in `monitor/health_notify.py`.

Every ten minutes, after local time reaches 22:00:

1. Set `target_date = local_today + one day`.
2. Read `daily_planned_profit.json`.
3. If `days[target_date]` is absent, send one immediate
   `notify.notify` message for that target date.
4. Persist the sent signature in the existing
   `monitor_alert_state.json` state file.
5. If a late MPC creates the target key before midnight, clear the pending
   signature. Do not send a recovery notification.

Use a dedicated planned-profit deadline path. Do not route this alert through
the normal recoverable MPC path, because that path has a 60-minute phone
escalation delay. After 22:00 there is little useful time left for the normal
day-ahead lock.

Suggested message:

```text
No EMHASS planned-profit lock exists for tomorrow (2026-08-17). A day-ahead price forecast or a successful MPC may be missing.
```

The message must not claim that prices were definitely absent. A missing lock
can also result from a failed MPC, a result-schema failure, or an HA write
failure.

Alert rules:

- Signature is `planned_profit_missing:<target_date>`.
- One successful delivery is enough for that target date.
- A delivery failure does not mark the signature as sent; retry on the next
  monitor poll.
- A new target date gets a new signature.
- The alert uses `notify.notify` only. It does not use WUD or Telegram.
- If the JSON is missing, treat the target as unlocked. If it is malformed,
  use a message that says the lock record is invalid and keep the same
  once-per-target rule.

## Implementation files

| File | Change |
|---|---|
| `emhass-cron/fetch_prices_emhass.py` | Add one price snapshot contract, result reader, date grouping, write-once JSON lock, helper publish, and auxiliary run status |
| `emhass-cron/tests/test_fetch_prices_emhass.py` | Test snapshot reuse, local date grouping, result validation, persistence, no overwrite, prune, and auxiliary failure behavior |
| `monitor/health_notify.py` | Add the once-per-target missed-lock check and notification delivery state |
| `monitor/tests/test_health_notify.py` | Test 22:00 behavior, deduplication, late lock clearing, malformed state, and delivery retry |
| `home-assistant/ha_emhass_helpers.yaml` | Add the 255-character JSON transport helper |
| `home-assistant/ha_emhass_dashboard.yaml` | Add the helper-backed planned-profit template sensor |
| `home-assistant/dashboards/emhass_trading.yaml` | Add the planned value and the two-series seven-day graph |
| `scripts/tests/test_ha_emhass_dashboard.py` or the existing dashboard tests | Test the sensor contract and dashboard structure |
| `.gitignore` | Ignore the live planned-profit JSON and lock file |
| `emhass-cron/README.md` | Document the lock source, schema, and helper transport |

No change is required to `docker-compose.yml`, the deploy manifest, or the
Dockerfiles. The required Docker socket, container name, shared `/share`
volume, and package upload paths already exist.

## Error handling and safety

- Price fetch failure still aborts the MPC. Never use stale CSV data to make a
  planned lock.
- No lock is made from a failed, dry-run, write-only, publish-only,
  SOC-refresh, or weather-only action.
- A result read or planned-profit publish failure does not invalidate an
  already successful MPC. It writes a `planned_profit` failure block and
  retries on the next successful MPC.
- Missing `cost_profit`, invalid timestamps, non-finite values, or no target
  date rows produce no lock and no zero fallback.
- The existing MPC success marker and control gate remain unchanged.
- A later plan cannot change an earlier locked date.
- All calendar grouping, alert deadlines, and sensor date checks use
  `Europe/Amsterdam`. Use timezone-aware timestamps at DST transitions.
- Do not add an automatic container update or any WUD action.

## Verification

### Unit and structural tests

1. Price snapshot tests prove that one HA forecast response feeds both CSV
   writing and tomorrow detection.
2. Result tests cover positive and negative `cost_profit`, local date grouping,
   DST boundaries, missing columns, malformed timestamps, non-finite values,
   and zero matching rows.
3. Persistence tests cover atomic write, write-once behavior, prune, and an
   overlapping-writer case.
4. Main-flow tests prove that the lock runs only after successful MPC
   finalization and that auxiliary lock errors do not change `mpc.ok`.
5. Helper tests prove compact JSON stays within 255 characters and uses the
   HA `input_text.set_value` service.
6. Monitor tests prove one notification per target date, retry after delivery
   failure, late-lock clearing, malformed-file handling, and DST-safe 22:00
   behavior.
7. HA structural tests prove the new template sensor contract, the Prices
   card entity, and the two graph series.
8. Run:

```bash
rtk .venv/bin/python -m unittest discover -s emhass-cron/tests -p 'test_*.py' -v
rtk .venv/bin/python -m unittest discover -s monitor/tests -p 'test_*.py' -v
rtk .venv/bin/python -m unittest scripts.tests.test_emhass_trading_dashboard -v
rtk ./scripts/render-ha-packages.sh
```

### Live acceptance after an approved deploy

- The first successful MPC with tomorrow price rows creates one tomorrow key
  with `planned_eur`, `slot_count`, and coverage attributes.
- A later successful MPC does not change that key.
- On the target date, the planned sensor shows the locked value and exposes
  the stored coverage range.
- If the target date has no lock at 22:00, the monitor sends one notification.
- A late lock before midnight stops further notifications for that target.
- The seven-day graph shows both series without changing EMHASS control or
  the realized-profit formula.

## Out of scope

- A complete 24-hour planned-profit value.
- Changing `prediction_horizon`, MPC cadence, price handling, or weather
  handling.
- Rebuilding missing locks from Home Assistant recorder history.
- Merging later MPC runs into an existing lock.
- Telegram, WUD, or automatic container updates.
- Changing the realized profit formula or Zonneplan source sensors.
- Reterminal firmware or a new MQTT transport.

## Related

- Realized profit: `docs/superpowers/specs/2026-07-23-emhass-net-electricity-profit-design.md`
- Seven-day realized graph: `docs/superpowers/specs/2026-08-01-emhass-7d-net-profit-graph-design.md`
- Price path: `emhass-cron/fetch_prices_emhass.py` ->
  `sensor.zonneplan_current_electricity_tariff` `forecast`
