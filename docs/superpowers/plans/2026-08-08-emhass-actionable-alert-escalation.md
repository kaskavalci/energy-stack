# EMHASS Actionable Alert Escalation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task by task. Each task has checkbox steps for tracking.

**Goal:** Make Home Assistant phone notifications actionable. Record recoverable and intentional events in a native dashboard event log. Send a phone notification only when a system fault remains after its recovery deadline and operator action is required.

**Architecture:** Keep existing control and safety decisions. Add one event-recording path in Home Assistant. Add reason and phase context to D2 native fallback. Change the energy-host monitor from immediate phone notification for recoverable faults to event logging followed by deadline-based escalation. Add a small native Sigen diagnostics dashboard with a 48-hour Logbook panel.

**Tech Stack:** Home Assistant YAML/Jinja, Logbook and Recorder, Python standard library, existing HA REST notification path, `unittest`, `ruamel.yaml`, and the repository render/deploy scripts.

## Global Constraints

- Phone notifications are for incidents that are not self-healing and need operator action.
- First detection, intentional control changes, recovery, and duplicate symptoms are dashboard events only.
- The existing alert thresholds remain detection thresholds. Escalation adds a deadline; it does not hide a real degraded state.
- Use these escalation deadlines:
  - MPC or load-history faults: two missed 30-minute MPC slots, or 60 minutes without usable control.
  - Weather, price, or optimiser faults: two consecutive scheduled failures, or 60 minutes without a valid MPC result.
  - Sensor or MQTT faults: 30 minutes stale while the affected function remains impaired.
  - D2 intentional contexts (`master_off`, `startup_or_reconnect`, `idle`, `soc_floor`, and `recovery`): dashboard event only.
  - D2 real control contexts (`stale_plan` and `bad_optim`): log the first timeout; escalate after a second same-context timeout within 10 minutes while native mode or limits remain unconfirmed.
  - Recovery-stalled incidents keep the existing 2-hour active-recovery and 6-hour below-floor deadlines.
- Preserve EMHASS invariants: 30-minute optimisation step, maximum prediction horizon 32, CSV prices, Open-Meteo weather, weather cache max age 150 minutes, and abort MPC on price failure.
- Do not change the control decision or fallback safety behaviour while changing alert routing.
- Keep `script.emhass_push_alert` as the existing phone-alert interface. Add a separate event logger.
- Use a native Home Assistant `logbook` card. Do not add HACS or custom dashboard cards.
- Use `input_text.emhass_alert_event` as the stable Logbook target for synthetic events. Every event written by HA or the energy-host monitor must include this entity.
- Use existing HA service or REST paths. Do not add a new MQTT topic, API, or external service.
- Use `config/site.env` for hosts and entity IDs. Do not add site values, secrets, device serials, or hardcoded host addresses to tracked files.
- Use additive HA configuration merge and the existing render and deploy scripts.
- The existing `feature/sigen-diagnostics-dashboard` work is a prerequisite for Task 6. Merge it to `main`, or make its exact dashboard, package, merge, deploy, and test files available in the working tree before Task 6 starts. Do not create a second diagnostics dashboard or a second registration path.
- Read `memory-bank/activeContext.md`, `memory-bank/techContext.md`, and `.agents/skills/emhass-energy-stack/SKILL.md` before implementation or deployment.
- Do not create a git commit unless the user explicitly requests it.
- For C1, C2a, C2b, E1, and E2, the escalation clock starts when the related fail binary sensor changes to `on`. Log the detection at that transition. Do not stack the current `for:` delay with the 30-minute deadline: remove the detection `for:` delay or set it to zero, then phone at `on` plus 30 minutes only if the binary sensor is still `on` and the affected function is still impaired. Any internal stale threshold in the binary sensor remains part of detection.

---

## File Map

Create or modify these files:

- `home-assistant/ha_emhass_monitoring.yaml` — event anchor, event logger, and notification routing.
- `home-assistant/ha_sigen_ems.yaml` — D2 reason, phase, signature, and timeout escalation context.
- `monitor/health_notify.py` — deadline state, event logging, escalation, deduplication, and accurate messages.
- `monitor/tests/test_health_notify.py` — monitor state and escalation tests.
- `scripts/tests/test_sigen_ems_package.py` — D2 reason, phase, and message tests.
- `scripts/tests/test_emhass_actionable_alerts.py` — HA event contract and automation routing tests.
- `home-assistant/dashboards/sigen_diagnostics.yaml` — existing diagnostics dashboard; extend it with the event log after the prerequisite branch is merged.
- `home-assistant/ha_sigen_diagnostics.yaml` — existing diagnostics package supplied by the prerequisite branch; preserve its MQTT health and liveness entities.
- `scripts/tests/test_sigen_diagnostics_dashboard.py` — existing dashboard structural tests; extend them for the event panel.
- `scripts/lib/ha_config_merge.py`, `configuration.yaml`, and `scripts/deploy-home-assistant.sh` — existing prerequisite wiring; verify it rather than creating a second registration path.
- `scripts/tests/test_ha_config_merge.py` — existing merge test; extend only if the prerequisite wiring is not already covered.
- `docs/superpowers/specs/2026-08-08-emhass-actionable-alert-escalation-design.md` — approved design reference; update only if implementation decisions materially change.
- `memory-bank/activeContext.md` and `memory-bank/techContext.md` — update after implementation and deployment verification.

## Task 1: Add failing tests for the approved alert contract

**Files:** `monitor/tests/test_health_notify.py`, `scripts/tests/test_sigen_ems_package.py`, `scripts/tests/test_emhass_actionable_alerts.py`, `scripts/tests/test_ha_config_merge.py`

- [ ] Read the current memory files, EMHASS skill, monitor tests, Sigen package tests, and HA merge tests.
- [ ] Add tests for the HA event contract:
  - `input_text.emhass_alert_event` exists with a bounded length.
  - `script.emhass_log_event` accepts `incident_id`, `phase`, `message`, and optional `action`.
  - The logger writes `logbook.log` against the event anchor.
  - Diagnostic transitions call the logger and do not call the phone notifier.
- [ ] Add tests for monitor escalation:
  - first recoverable detection logs without a phone notification;
  - a recoverable MPC incident escalates at 60 minutes;
  - the same incident signature does not repeat the phone notification;
  - recovery logs without a recovery phone notification;
  - the B6 message distinguishes stale data from missing data;
  - a Logbook failure does not clear the incident state.
- [ ] Add tests for D2 context:
  - the restore script declares a reason field;
  - every restore caller passes a reason;
  - timeout text uses live master, mode, and limit state;
  - the signature contains reason and phase;
  - intentional reasons do not use the phone-alert path.
- [ ] Defer dashboard assertions to Task 6, after the diagnostics prerequisite is available. Do not create a new dashboard test file or a second dashboard registration path.
- [ ] Use the existing `scripts/tests/test_ha_config_merge.py` to verify that the prerequisite registration preserves third-party dashboards. Add only a missing assertion; do not add a second dashboard registration test path.
- [ ] Run the focused new tests with `rtk env PYTHONPATH=scripts/lib python3 -m unittest discover -s scripts/tests -p 'test_emhass_actionable_alerts.py'` and `rtk env PYTHONPATH=lib:monitor/tests python3 -m unittest discover -s monitor/tests -p 'test_health_notify.py'`; confirm the new tests fail for the missing behaviour.

## Task 2: Add the Home Assistant event recorder

**Files:** `home-assistant/ha_emhass_monitoring.yaml`, `scripts/tests/test_emhass_actionable_alerts.py`

- [ ] Add `input_text.emhass_alert_event` with `max: 255` for a stable Logbook entity state and Recorder storage.
- [ ] Add `script.emhass_log_event` with exact fields:
  - `incident_id: str`;
  - `phase: str`;
  - `message: str`;
  - `action: str` with an empty default.
- [ ] Make the script set the event anchor to a bounded event string, then call `logbook.log` with `entity_id: input_text.emhass_alert_event`.
- [ ] Include incident ID, phase, message, and action in the Logbook message. Truncate only the anchor state if needed; keep the Logbook message useful for diagnosis.
- [ ] Route all HA diagnostic transitions through this script. Include detection, deadline, suppressed duplicate, and recovery events.
- [ ] Keep `script.emhass_push_alert` for phone notifications only. Do not make the event logger call `notify.notify`.
- [ ] Run the focused HA contract tests and YAML/render checks.

## Task 3: Add D2 reason and same-context escalation

**Files:** `home-assistant/ha_sigen_ems.yaml`, `scripts/tests/test_sigen_ems_package.py`

- [ ] Add persistent D2 helper entities:
  - `input_text.emhass_d2_failure_signature` with `max: 255`;
  - `input_datetime.emhass_d2_last_failure`;
  - `input_number.emhass_d2_failure_count` with integer values from 0 to 2 and initial value 0.
- [ ] Add a `reason` field to `sigen_restore_native_control`, with `unknown` as the explicit default.
- [ ] Pass these reasons from every current caller:
  - master-off or kill-switch path: `master_off`;
  - startup or reconnect path: `startup_or_reconnect`;
  - idle or deadband path: `idle`;
  - SOC-floor cutout: `soc_floor`;
  - recovery path: `recovery`;
  - stale-plan path: `stale_plan`;
  - bad-optimisation path: `bad_optim`.
- [ ] In `emhass_sigen_disable_on_stale_plan`, remove the unconditional `script.emhass_push_alert` action. Log the detection with `script.emhass_log_event`; the restore script owns the first-timeout log and the approved second-timeout escalation for `stale_plan`.
- [ ] In `emhass_sigen_disable_on_bad_optim`, remove the unconditional `script.emhass_push_alert` action. Call `sigen_restore_native_control` with `reason: bad_optim`; log the detection and allow a phone notification only through the approved same-context timeout path.
- [ ] Add structural tests scoped to both automation blocks. They must prove that their happy-path actions do not call `script.emhass_push_alert` and that the restore call passes the correct reason.
- [ ] Define the D2 signature as reason plus phase, where phase distinguishes native-mode confirmation from limit confirmation.
- [ ] On the first timeout, write a dashboard event with the live master state, selected mode, and limit states. Do not send a phone notification.
- [ ] On a second timeout with the same signature within 10 minutes, escalate only for `stale_plan` or `bad_optim` when master is on and safe fallback remains unconfirmed.
- [ ] Deduplicate the escalated phone notification by signature. Log suppressed duplicates.
- [ ] Reset the D2 count and signature after successful native-mode and limit confirmation. Log one resolved event.
- [ ] Keep the existing mode selection, limit writes, waits, and `mode: restart` behaviour unchanged.
- [ ] Replace the hardcoded “Master is off” timeout message with values read from the current entities.
- [ ] Run the focused Sigen tests and render the package before continuing.

## Task 4: Route Home Assistant alerts by operator actionability

**Files:** `home-assistant/ha_emhass_monitoring.yaml`, `scripts/tests/test_emhass_actionable_alerts.py`

- [ ] Change A2, A3, A5, and D1 detection notifications to event-log entries. The energy-host monitor remains the owner of the related MPC or load-history escalation.
- [ ] For C1, C2a, C2b, E1, and E2, log first detection and send a phone notification only after 30 minutes when the affected function is still impaired.
- [ ] Start the C1, C2a, C2b, E1, and E2 escalation timer on the fail-binary `on` transition. Remove or set the current detection `for:` values (30, 10, 10, 15, and 15 minutes) to zero so they do not add a second delay. Send the phone notification at `on` plus 30 minutes only when the fail binary remains `on` and the affected function is still impaired.
- [ ] Add a parameterized timing test covering C1 PV, C2a house load, C2b SOC, E1 P1, and E2 Sigen MQTT. Assert that an impairment at T is log-only before T+30 and phone-actionable at T+30 when it remains impaired.
- [ ] Give every escalation automation an explicit incident ID, state condition, deadline, and action message.
- [ ] Keep D3 phone escalation at the existing 2-hour active-recovery or 6-hour below-floor deadline. Add event logging and duplicate suppression around it.
- [ ] Do not send a recovery phone notification. Log recovery with the duration and the restored function.
- [ ] Ensure intentional master-off, idle, SOC-floor, startup, reconnect, and normal recovery paths are event-only.
- [ ] Run the focused HA tests and inspect rendered YAML for duplicate automation IDs and invalid service data.

## Task 5: Add deadline state and event logging to the energy-host monitor

**Files:** `monitor/health_notify.py`, `monitor/tests/test_health_notify.py`

- [ ] Extend the persisted alert state with explicit first-detected time, phase, escalation deadline, and notification signature. Preserve compatibility with existing `monitor_alert_state.json` values.
- [ ] Add small, testable functions with explicit inputs and outputs:
  - `call_ha_service(service, data)` for the existing HA REST service path;
  - `log_ha_event(incident_id, phase, message, action)` calling `script.emhass_log_event`;
  - `incident_signature(alert_id, context)` returning a stable deduplication key;
  - `record_incident(...)` updating state and writing the first detection event;
  - `should_escalate(...)` applying the alert deadline and current impairment.
- [ ] Treat B2, B3, B4, B5, and B6 as recoverable incidents: log first detection, escalate after 60 minutes of continued impairment, and log recovery without phone notification.
- [ ] Preserve immediate phone escalation for hard service failures that need operator action on the existing monitor schedule.
- [ ] Change B6 transition handling so a load-history degradation is not an immediate phone notification. Use accurate “stale sample” or “missing sample” text based on the observed state.
- [ ] Keep the existing unsafe-load-history suppression of derivative B2, B3, and B5 symptoms, while recording the suppressed symptoms in the event log.
- [ ] Make event-log and phone-service failures observable. Do not clear an incident, reset its deadline, or mark it recovered when a notification service call fails.
- [ ] Preserve recovery deduplication and stable incident signatures across monitor runs.
- [ ] Run the monitor unit tests, including a state-file migration test, and confirm no unrelated alert logic regresses.

## Task 6: Extend the existing Sigen diagnostics dashboard

**Prerequisite:** Merge `feature/sigen-diagnostics-dashboard` to `main`, or provide its merge-ready files in the working tree. Confirm that `home-assistant/dashboards/sigen_diagnostics.yaml`, `home-assistant/ha_sigen_diagnostics.yaml`, its merge/deploy wiring, and `scripts/tests/test_sigen_diagnostics_dashboard.py` are present before editing.

**Files:** `home-assistant/dashboards/sigen_diagnostics.yaml`, `scripts/tests/test_sigen_diagnostics_dashboard.py`; use existing merge/deploy files from the prerequisite branch without creating duplicate registrations

- [ ] Preserve the existing battery health, status and alarms, bridge liveness, bridge metrics, temperature statistics, and bridge history cards.
- [ ] Add the event panel near the bottom of the existing diagnostics view. Do not replace the view or create a second view.
- [ ] Add this Logbook card exactly as the event panel:

  ```yaml
  - type: logbook
    title: EMHASS events
    target:
      entity_id:
        - input_text.emhass_alert_event
    hours_to_show: 48
    name_detail: entity
  ```

- [ ] Verify that the prerequisite branch already registers `sigen-diagnostics` in `ENERGY_STACK_MANIFEST`, the example `configuration.yaml`, and the deploy upload list. Edit those files only if the merged prerequisite is incomplete; never add a second dashboard ID or filename.
- [ ] Extend `test_sigen_diagnostics_dashboard.py` to prove the event card exists and all existing health/liveness cards remain.
- [ ] Run the existing dashboard and merge tests.

## Task 7: Run repository verification

**Files:** rendered output from the repository render script; no source files beyond the files listed above

- [ ] Read `config/site.env` before any live command. Do not print secrets.
- [ ] Run `rtk ./scripts/render-ha-packages.sh`.
- [ ] Run `rtk PYTHONPATH=scripts/lib python3 -m unittest discover -s scripts/tests -p 'test_*.py'`.
- [ ] Run all monitor tests with `rtk env PYTHONPATH=lib:monitor/tests python3 -m unittest discover -s monitor/tests -p 'test_*.py'`.
- [ ] Run `rtk git diff --check`.
- [ ] Search rendered and changed files for unresolved template placeholders and invalid empty entity IDs.
- [ ] Inspect the final diff for minimal scope. Do not stage or commit changes.

## Task 8: Deploy and verify only after implementation approval

**Files:** no source changes expected; update memory bank after verified deployment

- [ ] Confirm the user wants live deployment before running deploy scripts.
- [ ] Run the existing HA deploy script with values from `config/site.env`: `rtk ./scripts/deploy-home-assistant.sh`.
- [ ] Deploy the energy-host monitor only when its source changes are included and the user approves that live change: `rtk ./scripts/deploy-energy-stack.sh`.
- [ ] Confirm the HA container configuration check passes and the existing compose restart completes.
- [ ] Open the Sigen diagnostics dashboard and confirm the event panel shows a test diagnostic event.
- [ ] Confirm a healthy or intentional D2 path writes a dashboard event and sends no phone notification.
- [ ] Confirm a persistent, actionable incident reaches its deadline and sends one phone notification with an operator action.
- [ ] Confirm recovery writes one event and sends no recovery phone notification.
- [ ] Update `memory-bank/activeContext.md` and `memory-bank/techContext.md` with implementation and deploy status.
- [ ] Do not create a commit unless the user explicitly requests one.

## Completion Criteria

- Every current alert has a documented detection condition and an operator-action deadline or is explicitly event-only.
- A healthy, intentional, or self-healing condition cannot create a phone notification.
- A persistent actionable condition creates one phone notification with a clear action and no repeated noise.
- D2 messages report live state and the actual restore reason. They do not claim that master is off when it is on.
- The Sigen diagnostics dashboard shows the last 48 hours of events through the native Logbook card.
- Focused tests, full repository tests, rendered YAML checks, and `git diff --check` pass.
