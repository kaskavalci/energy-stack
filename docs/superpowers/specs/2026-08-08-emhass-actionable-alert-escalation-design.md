# EMHASS actionable alert escalation

**Date:** 2026-08-08  
**Status:** Design approved in conversation; implementation pending  
**Scope:** Home Assistant and energy-monitor phone-alert policy

## Problem

The current stack has useful technical thresholds, but it treats many
threshold crossings as phone-worthy incidents. This creates noise when the
system is safely recovering or operating in native fallback.

The 24-hour sample contained 17 phone pushes:

- 11 repeated D2 native-fallback messages;
- one C2a transient input alert;
- one B6 unsafe load-history alert and one recovery message;
- D1 and A5 messages caused by the same load-history episode;
- one D2 Remote EMS message.

D2 is also factually misleading. `script.sigen_restore_native_control` is
called for master-off, idle/deadband, SOC protection, reconnect, stale-plan,
and optimizer paths, but its timeout message always says that master is off.
The script is retried from normal five-minute plan reconciliation, so one
technical timeout can become a notification storm.

## Goals

1. Phone notifications mean that operator action is required.
2. Safe, intentional, and self-healing states remain visible in the
   dashboard/logbook without phone pushes.
3. One owned root incident produces at most one phone notification until
   recovery.
4. Messages describe observed state, impact, and an explicit `Action:`.
5. D2 identifies why native restoration was requested and never makes an
   absolute master-state claim from a shared script path.
6. Operators can inspect a focused event list from the Sigen diagnostics
   dashboard.
7. Control safety behavior and EMHASS operating invariants remain unchanged.

## Non-goals

- No changes to EMHASS MPC calculations, price handling, weather handling, or
  native-load safety gates.
- No changes to Sigenergy control decisions beyond notification gating and
  diagnostic context.
- No new MQTT transport or external incident service.
- No phone notification for recovery completion.

## Operator notification contract

Existing technical thresholds remain detection thresholds. They create a
dashboard/logbook event and start or continue recovery tracking.

A phone notification requires all applicable conditions below:

1. The condition is not intentional, such as master-off or SOC-floor
   protection.
2. The normal recovery or fallback path was attempted.
3. The recovery deadline was exceeded, or a safe fallback could not be
   confirmed.
4. The affected service remains materially impaired.
5. No higher-priority root incident already owns the notification.

The incident lifecycle is:

```text
detected -> recovering -> actionable -> resolved
```

Only the transition to `actionable` can notify the phone. Detection,
recovering, and resolved transitions are dashboard/logbook events.

Repeated checks for the same incident signature are deduplicated. A changed
root cause or materially changed incident signature may create one new
actionable notification.

## Alert ownership and root-cause policy

Alert ownership prevents HA symptoms and energy-host symptoms from producing
parallel phone notifications.

### Energy-host owner: energy-monitor

The energy-monitor owns phone escalation for:

- EMHASS/container availability;
- MPC, price, weather, and optimizer run failures;
- stale MPC success state;
- Open-Meteo cache and zero-fill problems;
- native load-history incidents.

HA A2, A3, and A5 phone pushes become dashboard/logbook diagnostics for these
energy-host conditions. The monitor emits the actionable root notification
after its escalation deadline.

### Plant/control owner: Home Assistant

HA owns phone escalation for:

- Sigen MQTT/entity availability when the affected plant function remains
  unavailable;
- native fallback confirmation failure after a real control failure;
- recovery stalled beyond the existing recovery deadlines;
- other actuator or safety-state failures that the energy-monitor cannot see.

HA D1 and the D2 Remote EMS symptom are diagnostics unless their owning
control incident reaches the actionable state. Related D1/D2/A5 symptoms are
suppressed when a higher-priority owner has identified the root cause. Only
one owner may phone for a given incident class; genuinely independent
simultaneous incidents may still notify separately.

## Proposed escalation deadlines

These deadlines are additional escalation gates. Existing detector thresholds
remain useful for state sensors and logbook visibility.

| Incident class | Detection | Phone escalation |
| --- | --- | --- |
| MPC, stale plan, or unsafe load history | Existing run and freshness checks | Two consecutive missed 30-minute MPC slots, 60 minutes total, while useful control remains unavailable |
| Weather, price, or optimizer failure | Existing failed-run checks | Two consecutive scheduled failures or 60 minutes without a valid MPC |
| Sensor or MQTT stale/unavailable | Existing stale/unavailable checks | Stale for 30 minutes and the affected MPC/control function remains impaired |
| D2 native restore, intentional path | Existing 75-second confirmation wait | Never for master-off, idle/deadband, SOC-floor, recovery, startup, or reconnect paths |
| D2 native restore, real control-failure path | Existing 75-second confirmation wait | First timeout is diagnostic; notify after a second same-context timeout within 10 minutes while native mode/limits remain unconfirmed |
| Recovery stall | Existing two-hour active-recovery or six-hour below-floor thresholds | Keep existing phone escalation, deduplicated |
| Hard service failure or unconfirmed safe fallback | Existing 10–15 minute HA holds or 10-minute monitor checks | Notify when that check still cannot prove service or safe fallback; one incident notification |

The first 24-hour sample would therefore produce no phone notifications:
the D2 events were on a normal shared/idle path, B6 recovered before the
60-minute escalation budget, C2a recovered, and D1/A5 were derivatives.
A genuinely persistent outage would still produce one actionable notification.

## Home Assistant design

### D2 context

Every caller of `script.sigen_restore_native_control` supplies a restore
reason from this set:

- `master_off`;
- `idle`;
- `soc_floor`;
- `recovery`;
- `startup_or_reconnect`;
- `stale_plan`;
- `bad_optim`.

The script records the reason in the logbook and uses the live master state,
EMS mode, and limit states in diagnostic messages. It never emits the
absolute statement `Master is off` unless the live master state is off.

The first timeout records a recovering event. A small persistent HA incident
state tracks the reason and repeated timeout window. Only a real control
failure reason with a second timeout inside 10 minutes and unconfirmed native
mode/limits can invoke the phone notification. Intentional and self-healing
reasons remain dashboard-only.

### HA notifications

Existing failure binary sensors remain available for dashboard status. Their
automations write logbook events at detection time. Phone actions are moved
behind the escalation conditions above.

The existing `script.emhass_push_alert(title, message)` interface remains the
phone-notification primitive. A separate logbook event path is used for
diagnostic transitions, so diagnostics cannot accidentally fan out to mobile
devices.

D1/A5/D2 messages include the current `block_reason`, the owning root cause,
and a concrete action. Safe reasons such as `master_off` and
`soc_below_floor` do not create phone notifications.

## Diagnosis dashboard event panel

Add a native Home Assistant Activity/Logbook card to the Sigen diagnostics
dashboard. In YAML, the card type remains `logbook` even though the UI calls
it Activity.

```yaml
- type: logbook
  title: EMHASS events
  target:
    entity_id:
      - input_text.emhass_alert_event
  hours_to_show: 48
  name_detail: entity
```

Place this card full-width near the bottom of the diagnostics view, after the
current status and history cards. No HACS or custom frontend card is needed.

Add `input_text.emhass_alert_event` as a stable event anchor. Every custom
`logbook.log` call from HA and every monitor diagnostic event written through
the HA API includes this entity. The event message contains:

- timestamp supplied by HA;
- incident ID and phase (`detected`, `recovering`, `actionable`, or
  `resolved`);
- root cause and observed state;
- operator action when the event is actionable.

The card shows the last 48 hours of EMHASS events. The built-in Activity
sidebar remains available for longer or broader Home Assistant history.

If Activity/logbook filtering excludes the anchor entity, the deployment must
retain the existing recorder/logbook inclusion needed for these events.

## Energy-monitor design

`monitor/health_notify.py` keeps incident state in the existing
`monitor_alert_state.json` file. State tracks the current incident signature,
first detection, escalation eligibility, last phone notification, and
recovery.

Recoverable first failures are logged and kept in state without a phone push.
The monitor escalates only when the incident-specific deadline is reached.
Same-signature checks are deduplicated. Recovery is logged once and does not
notify the phone.

Existing B6 load-history signature handling remains the basis for incident
identity. Derivative B2/B3/B5 failures remain suppressed when load-history is
the active root cause. The B6 message reports the actual failure reason and
does not claim missing bins when the failure is stale-sample validation.

The monitor may write diagnostic transitions to HA logbook through the
existing HA API service path. Failure to write a diagnostic log must not erase
incident state or prevent a future actionable notification.

## Error handling and safety

- Notification delivery failure does not mark an incident as delivered.
- Incident state persists across monitor restarts.
- HA restart or entity restoration must not turn intentional safe states into
  phone alerts.
- A failed diagnostic log call is non-fatal.
- A failed phone notification remains eligible for retry after the delivery
  error, subject to incident deduplication.
- Native fallback, SOC floor, recovery, master-off enforcement, and all
  existing fail-closed behavior remain unchanged.

## Verification plan

### Unit tests

Extend `monitor/tests/test_health_notify.py` for:

- first recoverable failure produces no phone notification;
- escalation after the incident deadline;
- same-signature deduplication;
- changed-signature escalation;
- recovery logging without phone notification;
- delivery failure retaining retry eligibility;
- load-history stale-sample messages reporting the real reason;
- derivative suppression for load-history incidents.

Extend HA package structural tests for:

- every D2 restore caller providing a reason;
- no hardcoded `Master is off` claim in shared D2 handling;
- intentional D2 paths having no phone action;
- explicit `Action:` text on actionable messages;
- D1/A5 diagnostic behavior and escalation ownership;
- the `input_text.emhass_alert_event` helper and event anchor on diagnostic
  logbook calls;
- unchanged native-control and safety scripts.

Extend dashboard structural tests for:

- one native `type: logbook` card titled `EMHASS events`;
- target `input_text.emhass_alert_event`;
- `hours_to_show: 48`;
- no custom card dependency for the event panel.

### Static and render checks

Run:

```bash
./scripts/render-ha-packages.sh
PYTHONPATH=scripts/lib python3 -m unittest discover -s scripts/tests -p 'test_*.py'
PYTHONPATH=lib:monitor/tests python3 -m unittest monitor.tests.test_health_notify
```

Verify rendered packages contain no unresolved placeholders. Run the existing
HA package, merge, and monitor tests relevant to the changed files.

### Replay and live verification

Replay the reported 24-hour sequence as a deterministic fixture. Expected
phone result: zero notifications for that healthy/recovering window.

Before deployment, review the rendered diff. After deployment, verify:

1. Master remains on and control remains healthy.
2. Idle/deadband native restores create logbook entries, not phone pushes.
3. A real repeated fallback failure creates one actionable D2 notification.
4. A transient load-history or sensor incident creates diagnostics only and
   no phone push when it recovers within budget.
5. A persistent MPC/control outage creates one notification with an explicit
   action.
6. Recovery clears incident state without a recovery push.

## Rollout and rollback

Deploy through the existing rendered additive HA package path and energy-stack
deployment path. Do not change EMHASS image, MPC, weather, price, or native
load configuration as part of this work.

If notification behavior is incorrect, restore the previous alert automation
and monitor image/configuration from the normal repository deployment backup.
Control behavior must remain enabled only under the existing master,
freshness, safety, and fallback gates.
