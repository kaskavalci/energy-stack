"""Additive merge for Home Assistant configuration.yaml deploy scripts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


class Include:
    """YAML !include tag value for Home Assistant packages."""

    __slots__ = ("path",)

    def __init__(self, path: str) -> None:
        self.path = path


def _register_include_tag(y: YAML) -> None:
    def include_constructor(loader, node):  # type: ignore[no-untyped-def]
        return Include(loader.construct_scalar(node))

    def include_representer(dumper, data: Include):  # type: ignore[no-untyped-def]
        return dumper.represent_scalar("!include", data.path)

    y.constructor.add_constructor("!include", include_constructor)
    y.representer.add_representer(Include, include_representer)


@dataclass(frozen=True)
class DeployManifest:
    """Keys this repo owns in live configuration.yaml."""

    name: str
    packages: dict[str, str] = field(default_factory=dict)
    lovelace_dashboards: dict[str, dict[str, Any]] = field(default_factory=dict)
    recorder_entity_globs: tuple[str, ...] = ()


RETERMINAL_MANIFEST = DeployManifest(
    name="reterminal",
    packages={
        "reterminal_energy": "!include ha_helpers.yaml",
        "reterminal_p1_mqtt": "!include ha_p1_mqtt_aliases.yaml",
    },
)

ENERGY_STACK_MANIFEST = DeployManifest(
    name="energy-stack",
    packages={
        "reterminal_emhass": "!include ha_emhass.yaml",
        "reterminal_emhass_sensors": "!include ha_emhass_sensors.yaml",
        "reterminal_emhass_dashboard": "!include ha_emhass_dashboard.yaml",
        "reterminal_emhass_monitoring": "!include ha_emhass_monitoring.yaml",
        "reterminal_emhass_helpers": "!include ha_emhass_helpers.yaml",
        "reterminal_sigen_ems": "!include ha_sigen_ems.yaml",
        "reterminal_sigen_diagnostics": "!include ha_sigen_diagnostics.yaml",
    },
    lovelace_dashboards={
        "emhass-trading": {
            "mode": "yaml",
            "title": "EMHASS & Sigen",
            "icon": "mdi:battery-charging-wireless",
            "show_in_sidebar": True,
            "filename": "dashboards/emhass_trading.yaml",
        },
        "sigen-diagnostics": {
            "mode": "yaml",
            "title": "Sigen diagnostics",
            "icon": "mdi:battery-heart-variant",
            "show_in_sidebar": True,
            "filename": "dashboards/sigen_diagnostics.yaml",
        },
    },
    recorder_entity_globs=("input_number.emhass_soc_*",),
)

MONOREPO_MANIFEST = DeployManifest(
    name="monorepo",
    packages={**RETERMINAL_MANIFEST.packages, **ENERGY_STACK_MANIFEST.packages},
    lovelace_dashboards=dict(ENERGY_STACK_MANIFEST.lovelace_dashboards),
    recorder_entity_globs=ENERGY_STACK_MANIFEST.recorder_entity_globs,
)


def _yaml_loader() -> YAML:
    y = YAML()
    _register_include_tag(y)
    y.preserve_quotes = True
    y.default_flow_style = False
    y.width = 4096
    return y


def _load_config(text: str) -> CommentedMap:
    y = _yaml_loader()
    data = y.load(text)
    if not isinstance(data, CommentedMap):
        raise ValueError("configuration.yaml root must be a mapping")
    return data


def _include_tag(value: str) -> str:
    if not value.startswith("!include "):
        raise ValueError(f"expected !include value, got: {value!r}")
    return value[len("!include ") :]


def _set_include_package(packages: CommentedMap, key: str, include_path: str) -> None:
    packages[key] = Include(include_path)


def _union_entity_globs(existing: list[Any] | None, owned: tuple[str, ...]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in existing or []:
        s = str(item)
        if s not in seen:
            seen.add(s)
            merged.append(s)
    for item in owned:
        if item not in seen:
            seen.add(item)
            merged.append(item)
    return merged


def merge_configuration(text: str, manifest: DeployManifest) -> str:
    """Upsert manifest-owned keys into configuration.yaml text."""
    data = _load_config(text)

    ha = data.get("homeassistant")
    if ha is None:
        ha = CommentedMap()
        data["homeassistant"] = ha
    if not isinstance(ha, CommentedMap):
        raise ValueError("homeassistant must be a mapping")

    packages = ha.get("packages")
    if packages is None:
        packages = CommentedMap()
        ha["packages"] = packages
    if not isinstance(packages, CommentedMap):
        raise ValueError("homeassistant.packages must be a mapping")

    for key, include_value in manifest.packages.items():
        _set_include_package(packages, key, _include_tag(include_value))

    if manifest.lovelace_dashboards:
        lovelace = data.get("lovelace")
        if lovelace is None:
            lovelace = CommentedMap()
            data["lovelace"] = lovelace
        if not isinstance(lovelace, CommentedMap):
            raise ValueError("lovelace must be a mapping")
        dashboards = lovelace.get("dashboards")
        if dashboards is None:
            dashboards = CommentedMap()
            lovelace["dashboards"] = dashboards
        if not isinstance(dashboards, CommentedMap):
            raise ValueError("lovelace.dashboards must be a mapping")
        for dash_id, dash_cfg in manifest.lovelace_dashboards.items():
            dashboards[dash_id] = CommentedMap(dash_cfg)

    if manifest.recorder_entity_globs:
        recorder = data.get("recorder")
        if recorder is None:
            recorder = CommentedMap()
            data["recorder"] = recorder
        if not isinstance(recorder, CommentedMap):
            raise ValueError("recorder must be a mapping")
        exclude = recorder.get("exclude")
        if exclude is None:
            exclude = CommentedMap()
            recorder["exclude"] = exclude
        if not isinstance(exclude, CommentedMap):
            raise ValueError("recorder.exclude must be a mapping")
        exclude["entity_globs"] = _union_entity_globs(
            exclude.get("entity_globs"), manifest.recorder_entity_globs
        )

    y = _yaml_loader()
    from io import StringIO

    buf = StringIO()
    y.dump(data, buf)
    return buf.getvalue()


def merge_configuration_file(live_path: Path, manifest: DeployManifest, out_path: Path) -> None:
    merged = merge_configuration(live_path.read_text(), manifest)
    out_path.write_text(merged)


def assert_manifests_disjoint() -> None:
    overlap = set(RETERMINAL_MANIFEST.packages) & set(ENERGY_STACK_MANIFEST.packages)
    if overlap:
        raise ValueError(f"package key overlap between manifests: {overlap}")
