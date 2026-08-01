# EMHASS 7-Day Net Profit Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a daily net electricity profit snapshot sensor and a last-7-days bar chart on the EMHASS trading dashboard.

**Architecture:** A trigger-based template sensor fires at 23:55 local time, records `production − delivery` from Zonneplan today cost sensors when both are numeric (condition skips the write otherwise), and a native `statistics-graph` bar card reads that sensor’s daily statistics.

**Tech Stack:** Home Assistant YAML packages + Lovelace; Python `unittest` structural tests; `ruamel.yaml` via existing dashboard tests; `./scripts/render-ha-packages.sh` / `./scripts/deploy-home-assistant.sh`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-01-emhass-7d-net-profit-graph-design.md`
- Formula: `net_profit = production_costs − delivery_costs` (Zonneplan **incl. tax**; never `*_excl_tax`)
- Entity: `sensor.emhass_net_electricity_profit_daily`
- Trigger: `at: "23:55:00"` (HA local / `Europe/Amsterdam`)
- Sensor: `unit_of_measurement: EUR`, `device_class: monetary`, `state_class: measurement`
- Attributes: `for_date`, `production`, `delivery`
- Bad sources at fire time → **skip update** (keep last good state); do not write `0`/`unknown` over a good day
- Dashboard: native `statistics-graph` only — no new ApexCharts / custom resources
- Forward-only: no backfill
- No energy-host / EMHASS MPC / cron changes
- Create implementation commits only when the user explicitly authorizes commits; otherwise leave diffs unstaged and report the suggested message
- Read `memory-bank/activeContext.md`, `memory-bank/techContext.md`, and `.agents/skills/emhass-energy-stack/SKILL.md` before live deploy work
- Source `config/site.env` before SSH/deploy; never hardcode hosts or tokens

## File Map

| File | Responsibility |
|------|----------------|
| `home-assistant/ha_emhass_dashboard.yaml` | Trigger sensor `emhass_net_electricity_profit_daily` |
| `home-assistant/dashboards/emhass_trading.yaml` | 7-day bar `statistics-graph` under Prices |
| `scripts/tests/test_sigen_ems_package.py` | Structural tests for daily sensor + month block boundary |
| `scripts/tests/test_emhass_trading_dashboard.py` | Structural test for profit graph card |
| `memory-bank/activeContext.md` | Note feature landed (and deploy status when verified) |
| `memory-bank/techContext.md` | One-line note that trading dashboard includes 7d net profit bars |

---

### Task 1: Failing structural tests (sensor + dashboard)

**Files:**
- Modify: `scripts/tests/test_sigen_ems_package.py`
- Modify: `scripts/tests/test_emhass_trading_dashboard.py`

**Interfaces:**
- Consumes: existing `EmhassNetElectricityProfitTests._sensor_block(name, next_name)`
- Produces: tests that require entity `sensor.emhass_net_electricity_profit_daily`, trigger `23:55:00`, condition skip-on-bad-sources, and dashboard card title `Net electricity profit — last 7 days`

- [ ] **Step 1: Update month sensor block boundary and add daily sensor tests**

In `scripts/tests/test_sigen_ems_package.py`, change `test_month_profit_is_production_minus_delivery` so the next sensor name is the daily sensor (not recovery):

```python
    def test_month_profit_is_production_minus_delivery(self) -> None:
        month = self._sensor_block(
            "EMHASS net electricity profit month",
            "EMHASS net electricity profit daily",
        )
        self.assertIn("default_entity_id: sensor.emhass_net_electricity_profit_month", month)
        self.assertIn(
            "sensor.zonneplan_electricity_production_costs_this_month", month
        )
        self.assertIn(
            "sensor.zonneplan_electricity_delivery_costs_this_month", month
        )
        self.assertIn("production - delivery", month)
        self.assertIn("unknown", month)
        self.assertNotIn("excl_tax", month)
```

Add this test method to the same class:

```python
    def test_daily_profit_snapshot_contract(self) -> None:
        package = self.dashboard
        self.assertIn("EMHASS net electricity profit daily", package)
        self.assertIn(
            "default_entity_id: sensor.emhass_net_electricity_profit_daily",
            package,
        )
        daily = self._sensor_block(
            "EMHASS net electricity profit daily",
            "EMHASS recovery target SOC",
        )
        self.assertIn('at: "23:55:00"', package)
        # Trigger block must sit above the daily sensor name in the file.
        trigger_idx = package.index('at: "23:55:00"')
        name_idx = package.index("EMHASS net electricity profit daily")
        self.assertLess(trigger_idx, name_idx)
        self.assertIn("unit_of_measurement: EUR", daily)
        self.assertIn("device_class: monetary", daily)
        self.assertIn("state_class: measurement", daily)
        self.assertIn(
            "sensor.zonneplan_electricity_production_costs_today", daily
        )
        self.assertIn(
            "sensor.zonneplan_electricity_delivery_costs_today", daily
        )
        self.assertIn("production - delivery", daily)
        self.assertIn("for_date:", daily)
        self.assertIn("production:", daily)
        self.assertIn("delivery:", daily)
        self.assertNotIn("excl_tax", daily)
        # Skip write when sources bad: template condition (not overwrite with unknown/0).
        condition_region = package[trigger_idx:name_idx]
        self.assertIn("condition:", condition_region)
        self.assertIn("float(none) is not none", condition_region)
```

- [ ] **Step 2: Add dashboard graph structural test**

In `scripts/tests/test_emhass_trading_dashboard.py`, add to `NativePvPanelTests` (or keep in the same class — it already has `trading_view()`):

```python
    def test_net_profit_7d_statistics_graph(self) -> None:
        cards = self.trading_view().get("cards", [])
        prices_idx = next(
            i
            for i, card in enumerate(cards)
            if isinstance(card, Mapping) and card.get("title") == "Prices"
        )
        graph = cards[prices_idx + 1]
        self.assertIsInstance(graph, Mapping)
        self.assertEqual(graph.get("type"), "statistics-graph")
        self.assertEqual(
            graph.get("title"), "Net electricity profit — last 7 days"
        )
        self.assertEqual(graph.get("days_to_show"), 7)
        self.assertEqual(graph.get("period"), "day")
        self.assertEqual(graph.get("chart_type"), "bar")
        self.assertEqual(graph.get("stat_types"), ["mean"])
        entities = graph.get("entities")
        self.assertIsInstance(entities, list)
        self.assertEqual(len(entities), 1)
        self.assertEqual(
            entities[0].get("entity"),
            "sensor.emhass_net_electricity_profit_daily",
        )
        self.assertEqual(entities[0].get("name"), "Net profit (EUR)")
```

- [ ] **Step 3: Run tests — expect FAIL**

```bash
python3 -m unittest \
  scripts.tests.test_sigen_ems_package.EmhassNetElectricityProfitTests \
  scripts.tests.test_emhass_trading_dashboard.NativePvPanelTests.test_net_profit_7d_statistics_graph \
  -v
```

Expected: FAIL — daily sensor name / `23:55:00` / graph card missing; month block split may also fail until the daily sensor exists between month and recovery.

- [ ] **Step 4: Commit tests only (if user authorized commits)**

```bash
git add scripts/tests/test_sigen_ems_package.py scripts/tests/test_emhass_trading_dashboard.py
git commit -m "$(cat <<'EOF'
test: add structural coverage for 7d net profit graph

EOF
)"
```

---

### Task 2: Daily snapshot sensor

**Files:**
- Modify: `home-assistant/ha_emhass_dashboard.yaml` (after month profit sensor, before recovery target SOC)

**Interfaces:**
- Consumes: `sensor.zonneplan_electricity_production_costs_today`, `sensor.zonneplan_electricity_delivery_costs_today`
- Produces: `sensor.emhass_net_electricity_profit_daily` with attrs `for_date`, `production`, `delivery`

- [ ] **Step 1: Insert trigger-based sensor block**

After the month profit sensor block (ends before `- name: EMHASS recovery target SOC`), close the current state-based `sensor:` list is **not** where this goes — add a **sibling** `template:` list entry with `trigger` + `condition` + `sensor`, matching `ha_emhass_sensors.yaml` house-load style.

Concrete placement options (pick one; prefer A):

**A (preferred):** Insert a new top-level list item under `template:` after the large `- sensor:` block that currently ends at recovery / binary sensors. Because recovery and binary sensors share the same `- sensor:` list today, do **not** split mid-list. Instead add the trigger block **immediately after** the month profit sensor by converting structure carefully:

Look at current structure:

```yaml
template:
  - sensor:
      - name: ... batt ...
      ...
      - name: EMHASS net electricity profit month
        ...
      - name: EMHASS recovery target SOC
        ...
  - binary_sensor:
      ...
```

Insert the daily snapshot as a **separate** `template` list item between the state sensors and binary sensors is wrong if recovery stays in the first list. Correct approach: add the trigger item **after** the entire first `- sensor:` list closes (after recovery sensor, before `- binary_sensor:`):

```yaml
  - trigger:
      - platform: time
        at: "23:55:00"
    condition:
      - condition: template
        value_template: >
          {{ states('sensor.zonneplan_electricity_production_costs_today') | float(none) is not none
             and states('sensor.zonneplan_electricity_delivery_costs_today') | float(none) is not none }}
    sensor:
      - name: EMHASS net electricity profit daily
        unique_id: emhass_net_electricity_profit_daily
        default_entity_id: sensor.emhass_net_electricity_profit_daily
        unit_of_measurement: EUR
        device_class: monetary
        state_class: measurement
        icon: mdi:cash-plus
        state: >
          {% set production = states('sensor.zonneplan_electricity_production_costs_today') | float(none) %}
          {% set delivery = states('sensor.zonneplan_electricity_delivery_costs_today') | float(none) %}
          {{ (production - delivery) | round(2) }}
        attributes:
          for_date: "{{ now().strftime('%Y-%m-%d') }}"
          production: >
            {{ states('sensor.zonneplan_electricity_production_costs_today') | float(none) }}
          delivery: >
            {{ states('sensor.zonneplan_electricity_delivery_costs_today') | float(none) }}
```

**Test helper note:** `_sensor_block("EMHASS net electricity profit daily", "EMHASS recovery target SOC")` assumes daily appears **before** recovery in the file text. With placement after recovery (before binary_sensor), that split breaks.

**Required placement for existing test helper:** put the trigger block **between month and recovery** by splitting the first `- sensor:` list:

1. End the first `- sensor:` list after the month sensor.
2. Insert the `- trigger: ... sensor: [daily]` block.
3. Start a new `- sensor:` list containing recovery target SOC (and nothing else that was after recovery in that list — only recovery is between month and binary_sensor today).

After edit, file order of names must be:

1. `EMHASS net electricity profit month`
2. `EMHASS net electricity profit daily` (inside trigger block)
3. `EMHASS recovery target SOC`

Condition + state still as shown above.

- [ ] **Step 2: Run sensor tests**

```bash
python3 -m unittest \
  scripts.tests.test_sigen_ems_package.EmhassNetElectricityProfitTests \
  -v
```

Expected: PASS for today, month, and daily contracts.

- [ ] **Step 3: Commit sensor (if user authorized commits)**

```bash
git add home-assistant/ha_emhass_dashboard.yaml scripts/tests/test_sigen_ems_package.py
git commit -m "$(cat <<'EOF'
feat(ha): snapshot daily net electricity profit at 23:55

EOF
)"
```

---

### Task 3: Dashboard 7-day bar graph

**Files:**
- Modify: `home-assistant/dashboards/emhass_trading.yaml` (immediately after the Prices `entities` card)

**Interfaces:**
- Consumes: `sensor.emhass_net_electricity_profit_daily`
- Produces: Lovelace card title `Net electricity profit — last 7 days`

- [ ] **Step 1: Insert statistics-graph card**

After the Prices card (ends around the month profit entity), before `Actual (Sigen / P1)`, insert:

```yaml
      - type: statistics-graph
        title: Net electricity profit — last 7 days
        days_to_show: 7
        period: day
        chart_type: bar
        stat_types:
          - mean
        entities:
          - entity: sensor.emhass_net_electricity_profit_daily
            name: Net profit (EUR)
```

Do not change Prices card contents. Do not add custom card types.

- [ ] **Step 2: Run dashboard + sensor tests**

```bash
python3 -m unittest \
  scripts.tests.test_sigen_ems_package.EmhassNetElectricityProfitTests \
  scripts.tests.test_emhass_trading_dashboard.NativePvPanelTests \
  -v
```

Expected: all PASS (including `test_net_profit_7d_statistics_graph` and existing custom-card allowlist).

- [ ] **Step 3: Render HA packages**

```bash
./scripts/render-ha-packages.sh
```

Expected: exit 0; rendered dashboard still contains the new graph title and entity id.

- [ ] **Step 4: Commit dashboard (if user authorized commits)**

```bash
git add home-assistant/dashboards/emhass_trading.yaml scripts/tests/test_emhass_trading_dashboard.py
git commit -m "$(cat <<'EOF'
feat(dashboard): show last 7 days net electricity profit bars

EOF
)"
```

---

### Task 4: Memory bank + deploy handoff

**Files:**
- Modify: `memory-bank/activeContext.md`
- Modify: `memory-bank/techContext.md`

**Interfaces:**
- Consumes: completed Tasks 1–3
- Produces: operator-visible note that graph is repo-ready; live verify after 23:55

- [ ] **Step 1: Update techContext**

In the trading-dashboard bullet in `memory-bank/techContext.md`, add that the dashboard also shows a native 7-day daily net profit bar graph from `sensor.emhass_net_electricity_profit_daily` (23:55 snapshot).

- [ ] **Step 2: Update activeContext**

Add a short section (date **2026-08-01**, author Auto/Composer): 7d net profit graph implemented in repo per spec `2026-08-01-emhass-7d-net-profit-graph-design.md`; not live-verified until HA deploy + first 23:55 snapshot.

- [ ] **Step 3: Deploy checklist (operator / this session if asked)**

```bash
source scripts/lib/source-site-env.sh
./scripts/deploy-home-assistant.sh
```

Post-deploy live checks (next calendar evening):

1. After 23:55: `sensor.emhass_net_electricity_profit_daily` is numeric; `for_date` is today’s date.
2. Trading view shows ≥1 bar on `Net electricity profit — last 7 days`.
3. Master/control behavior unchanged (this change is display-only).

- [ ] **Step 4: Commit memory bank (if user authorized commits)**

```bash
git add memory-bank/activeContext.md memory-bank/techContext.md
git commit -m "$(cat <<'EOF'
docs(memory-bank): note 7d net profit graph

EOF
)"
```

---

## Self-review (plan vs spec)

| Spec requirement | Task |
|------------------|------|
| Daily snapshot at 23:55 | Task 2 |
| Formula production − delivery, incl. tax | Task 2 |
| Skip write on bad sources | Task 2 `condition` |
| Attrs `for_date`, `production`, `delivery` | Task 2 |
| EUR / monetary / measurement | Task 2 |
| statistics-graph 7d day bar mean | Task 3 |
| Under Prices; today/month unchanged | Task 3 |
| No ApexCharts / no backfill / no MPC | Global + Tasks 2–3 |
| Structural tests + render | Tasks 1, 3 |
| Forward-only empty until first snapshot | Task 4 live checklist |

No placeholders remaining. Entity IDs and card titles match the spec verbatim.
