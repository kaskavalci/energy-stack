#!/usr/bin/env python3
"""Add sensor.electricity_meter_power_net to Energy detailed (storage) dashboard."""
from __future__ import annotations

import json
import sys
from pathlib import Path

NET_ENTITY = "sensor.electricity_meter_power_net"
NET_CARD = {
    "graph": "line",
    "hours_to_show": 24,
    "type": "sensor",
    "entity": NET_ENTITY,
    "name": "Grid net power (P1)",
    "detail": 1,
}


def patch(path: Path) -> bool:
    data = json.loads(path.read_text())
    cards = data["data"]["config"]["views"][0]["cards"]
    changed = False

    if not any(c.get("entity") == NET_ENTITY for c in cards if c.get("type") == "sensor"):
        insert_at = 6
        for i, c in enumerate(cards):
            if c.get("entity") == "sensor.electricity_meter_power_production":
                insert_at = i + 1
                break
        cards.insert(insert_at, NET_CARD)
        changed = True

    for c in cards:
        if c.get("type") != "entities":
            continue
        ents = c.setdefault("entities", [])
        ids = [e["entity"] if isinstance(e, dict) else e for e in ents]
        if NET_ENTITY in ids:
            continue
        for i, e in enumerate(ents):
            ent = e["entity"] if isinstance(e, dict) else e
            if ent == "sensor.electricity_meter_power_production":
                ents.insert(
                    i + 1,
                    {"entity": NET_ENTITY, "name": "Grid net power (P1)"},
                )
                changed = True
                break
        else:
            ents.insert(2, {"entity": NET_ENTITY, "name": "Grid net power (P1)"})
            changed = True

    if changed:
        path.write_text(json.dumps(data, indent=2) + "\n")
    return changed


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/lovelace.energy_detailed.json")
    if patch(target):
        print(f"Patched {target}")
    else:
        print(f"No changes needed for {target}")
